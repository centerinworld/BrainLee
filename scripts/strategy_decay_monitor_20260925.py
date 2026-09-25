#!/usr/bin/env python3
"""HANDOFF §12 R8: strategy performance-decay monitor -> strategy_decay_check (idempotent per check_date/strategy/window).

Recent 3/6/12-month return of each operating virtual account (virtual_strategy_equity_daily) vs the distribution of same-length rolling returns of the SAME
strategy's stitched backtest curve (strategy_center run-set; sc_v11 -> v11 ...). Flag when the recent return is below the 5th percentile of that distribution.
A window is evaluated only when the virtual history is at least that long (virtual accounts started 2026-05/06, so today only the 3-month window exists).
Caveat: the virtual return is measured on the whole 100M account, so a mostly-cash account (sc_* hold ~5-50% invested) shows returns near 0 and can never breach the
lower tail - only heavily invested accounts (v_gc, v_recovery, ...) are informative. Telegram alert for new flags; the API /api/research/strategy-decay exposes the table. Run monthly (scheduler, 1st business-day 06:30).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

MAP = {"sc_v2": "v2", "sc_v5": "v5", "sc_v8": "v8", "sc_v10": "v10", "sc_v11": "v11", "sc_golden_cross": "golden_cross",
       "sc_contract_momentum": "contract_momentum", "v_recovery": "recovery", "v_gc": "golden_cross", "v_contract_momentum": "contract_momentum",
       "turnover_100m": None, "sc_sector_focus": None}
WINDOWS = {"3M": 63, "6M": 126, "12M": 252}
DDL = """CREATE TABLE IF NOT EXISTS strategy_decay_check (
  check_date TEXT NOT NULL, strategy TEXT NOT NULL, win TEXT NOT NULL, backtest_strategy TEXT DEFAULT NULL, recent_ret_pct DOUBLE PRECISION DEFAULT NULL,
  p05_pct DOUBLE PRECISION DEFAULT NULL, p50_pct DOUBLE PRECISION DEFAULT NULL, pctile DOUBLE PRECISION DEFAULT NULL, n_hist INTEGER DEFAULT 0,
  flagged INTEGER DEFAULT 0, note TEXT DEFAULT '', created_at TIMESTAMP DEFAULT now(), PRIMARY KEY (check_date, strategy, win))"""


def backtest_returns(conn) -> dict[str, pd.Series]:
    rows = conn.execute("""SELECT s.strategy, b.start_date, e.date, e.equity FROM selected_run_registry s
      JOIN backtest_run_set_members m ON m.suite_hash=s.run_hash JOIN backtest_run_specs sp ON sp.run_hash=m.run_hash
      JOIN backtest_runs b ON b.run_id=sp.run_id JOIN backtest_equity_curve_best_v e ON e.run_id=sp.run_id
      WHERE s.report_type='strategy_center' ORDER BY 1,2,3""").fetchall()
    df = pd.DataFrame([tuple(r) for r in rows], columns=["strategy", "start", "date", "equity"])
    out = {}
    for strat, g in df.groupby("strategy"):
        parts = []
        for start, h in g.groupby("start"):
            h = h.sort_values("date")
            parts.append(pd.DataFrame({"r": h.set_index(pd.to_datetime(h.date)).equity.astype(float).pct_change().iloc[1:], "start": start}))
        p = pd.concat(parts).reset_index(names="date").sort_values(["date", "start"]).drop_duplicates("date", keep="last")
        out[strat] = p.set_index("date").r
    return out


def main(dry_run: bool = False) -> int:
    conn = connect_primary_db(timeout=300)
    if not dry_run:
        conn.execute(DDL)
        conn.commit()
    bt = backtest_returns(conn)
    eq = pd.DataFrame([tuple(r) for r in conn.execute("SELECT strategy, trade_date, equity FROM virtual_strategy_equity_daily ORDER BY 1,2").fetchall()],
                      columns=["strategy", "date", "equity"])
    eq["date"] = pd.to_datetime(eq.date)
    today = datetime_today()
    rows, flags = [], []
    for strat, g in eq.groupby("strategy"):
        bts = MAP.get(strat)
        s = g.set_index("date").equity.astype(float)
        for win, n in WINDOWS.items():
            if bts is None or bts not in bt:
                rows.append((today, strat, win, bts, None, None, None, None, 0, 0, "no backtest counterpart"))
                continue
            if len(s) <= n:
                rows.append((today, strat, win, bts, None, None, None, None, 0, 0, f"virtual history {len(s)}d < {n}d"))
                continue
            recent = (s.iloc[-1] / s.iloc[-1 - n] - 1) * 100
            cum = (1 + bt[bts]).cumprod()
            hist = ((cum / cum.shift(n) - 1) * 100).dropna()
            if len(hist) < 60:
                rows.append((today, strat, win, bts, float(recent), None, None, None, len(hist), 0, "backtest history too short"))
                continue
            p05, p50 = np.percentile(hist, 5), np.percentile(hist, 50)
            pct = float((hist < recent).mean() * 100)
            flag = int(recent < p05)
            rows.append((today, strat, win, bts, float(recent), float(p05), float(p50), pct, len(hist), flag, ""))
            if flag:
                flags.append(f"{strat}[{win}] {recent:+.1f}% < 하위5% {p05:+.1f}%")
    df = pd.DataFrame(rows, columns=["check_date", "strategy", "win", "backtest_strategy", "recent_ret_pct", "p05_pct", "p50_pct", "pctile", "n_hist", "flagged", "note"])
    print(df[df.recent_ret_pct.notna()].round(1).to_string(index=False) if df.recent_ret_pct.notna().any() else "no evaluable window")
    print("skipped:", df[df.recent_ret_pct.isna()].note.value_counts().to_dict())
    if dry_run:
        return 0
    conn.executemany(
        "INSERT INTO strategy_decay_check(check_date,strategy,win,backtest_strategy,recent_ret_pct,p05_pct,p50_pct,pctile,n_hist,flagged,note) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT (check_date,strategy,win) DO UPDATE SET recent_ret_pct=excluded.recent_ret_pct, p05_pct=excluded.p05_pct, p50_pct=excluded.p50_pct, "
        "pctile=excluded.pctile, n_hist=excluded.n_hist, flagged=excluded.flagged, note=excluded.note, created_at=now()",
        [tuple(None if (isinstance(v, float) and v != v) else v for v in r) for r in rows])
    conn.commit()
    if flags:
        try:
            import notifier
            notifier.send("⚠️ 전략 성과 감쇠 경고(백테스트 기대 분포 하위 5% 이탈): " + "; ".join(flags[:8]), key=f"decay_{today[:7]}")
        except Exception as exc:  # noqa: BLE001
            print("alert failed:", exc, file=sys.stderr)
    print("flagged:", flags)
    return 0


def datetime_today() -> str:
    from datetime import date
    return date.today().isoformat()


if __name__ == "__main__":
    sys.exit(main("--dry-run" in sys.argv))
