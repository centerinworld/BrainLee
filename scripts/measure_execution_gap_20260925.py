#!/usr/bin/env python3
"""HANDOFF §12 R7: execution-gap measurement -> execution_slippage_log (idempotent upsert, explicit DEFAULTs).

live_orders/live_fills are all mode=PAPER (simulated fills), so REAL broker slippage is not measurable yet. What IS measurable: the paper fill is priced at the
signal time (~18:37 on day D) while the backtests assume the NEXT trading day's OPEN. gap_open_pct = adverse-signed gap between the two:
  buy: (next_open / fill - 1) * 100 (we would pay more at the assumed price), sell: (fill / next_open - 1) * 100; positive = adverse, negative = favorable.
Only ENTRIES (buys) are judged against the assumption: exits are triggered intraday (e.g. 09:20 stops), the backtests do not assume next open for them, so sells are
recorded for reference but excluded from the alert.
Compared with the assumed one-way slippage of the market-cap tier (backtest_common._SLIP_TIERS: >=1조 0.1%, >=1000억 0.2%, >=100억 0.4%, else 0.8%).
Once real KIS fills exist (mode != PAPER) the same table records them with source='live'. Alerts (Telegram) when, over the last 30 days, n >= 20 and the mean
adverse gap exceeds the mean assumed slippage by >= 0.2%p (at most once per month).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

TIERS = [(10_000, 0.1), (1_000, 0.2), (100, 0.4), (0, 0.8)]
DDL = """CREATE TABLE IF NOT EXISTS execution_slippage_log (
  fill_id BIGINT PRIMARY KEY, order_id TEXT DEFAULT NULL, source TEXT DEFAULT 'paper', strategy_key TEXT DEFAULT NULL, stock_code TEXT DEFAULT NULL,
  side TEXT DEFAULT NULL, fill_ts TEXT DEFAULT NULL, fill_price DOUBLE PRECISION DEFAULT NULL, next_open DOUBLE PRECISION DEFAULT NULL,
  next_close DOUBLE PRECISION DEFAULT NULL, gap_open_pct DOUBLE PRECISION DEFAULT NULL, gap_close_pct DOUBLE PRECISION DEFAULT NULL,
  mcap_eok DOUBLE PRECISION DEFAULT NULL, assumed_slip_pct DOUBLE PRECISION DEFAULT NULL, measured_at TIMESTAMP DEFAULT now())"""


def tier(m):
    return next(r for t, r in TIERS if (m or 0) >= t)


def main(dry_run: bool = False) -> int:
    conn = connect_primary_db(timeout=300)
    if not dry_run:
        conn.execute(DDL)
        conn.commit()
    fills = conn.execute(
        "SELECT f.id, f.order_id, o.mode, o.strategy_key, o.stock_code, o.side, f.fill_ts, f.fill_price FROM live_fills f JOIN live_orders o ON o.order_id=f.order_id").fetchall()
    mc = {r[0]: r[1] for r in conn.execute("SELECT DISTINCT ON (stock_code) stock_code, market_cap FROM stock_universe ORDER BY stock_code, base_date DESC").fetchall()}
    rows = []
    for fid, oid, mode, strat, code, side, ts, price in fills:
        day = str(ts)[:10]
        nxt = conn.execute("SELECT open, close FROM price_history WHERE stock_code=? AND date::text > ? AND open>0 ORDER BY date LIMIT 1", (code, day)).fetchone()
        if not nxt or not price:
            continue
        o, c = float(nxt[0]), float(nxt[1] or 0)
        sgn = 1 if str(side).lower() == "buy" else -1
        gap_o = sgn * (o / float(price) - 1) * 100
        gap_c = sgn * (c / float(price) - 1) * 100 if c else None
        rows.append((int(fid), str(oid), "paper" if str(mode).upper() == "PAPER" else "live", strat, code, str(side), str(ts), float(price), o, c or None,
                     gap_o, gap_c, mc.get(code), tier(mc.get(code))))
    df = pd.DataFrame(rows, columns=["fill_id", "order_id", "source", "strategy_key", "stock_code", "side", "fill_ts", "fill_price", "next_open", "next_close",
                                     "gap_open_pct", "gap_close_pct", "mcap_eok", "assumed_slip_pct"])
    print(f"fills={len(fills)} measurable(next trading day exists)={len(df)}")
    if len(df):
        b = df[df.side.str.lower() == "buy"]
        print("BUY entries: n=%d mean adverse gap vs next open %%: %.3f | assumed one-way slippage %% (mean): %.3f" % (len(b), b.gap_open_pct.mean(), b.assumed_slip_pct.mean()))
        print(df.groupby("side").gap_open_pct.agg(["count", "mean", "median"]).round(3).to_string())
    if dry_run or not len(df):
        return 0
    conn.executemany(
        "INSERT INTO execution_slippage_log(fill_id,order_id,source,strategy_key,stock_code,side,fill_ts,fill_price,next_open,next_close,gap_open_pct,gap_close_pct,mcap_eok,assumed_slip_pct) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (fill_id) DO UPDATE SET next_open=excluded.next_open, next_close=excluded.next_close, "
        "gap_open_pct=excluded.gap_open_pct, gap_close_pct=excluded.gap_close_pct, mcap_eok=excluded.mcap_eok, assumed_slip_pct=excluded.assumed_slip_pct, measured_at=now()",
        [tuple(None if (isinstance(v, float) and v != v) else v for v in r) for r in rows])
    conn.commit()
    recent = df[(pd.to_datetime(df.fill_ts) >= pd.Timestamp.now() - pd.Timedelta(days=30)) & (df.side.str.lower() == "buy")]
    if len(recent) >= 20 and recent.gap_open_pct.mean() - recent.assumed_slip_pct.mean() >= 0.2:
        try:
            import notifier
            notifier.send(f"⚠️ 실행 괴리 경고: 최근 30일 {len(recent)}건 평균 역방향 괴리 {recent.gap_open_pct.mean():.2f}% > 가정 슬리피지 {recent.assumed_slip_pct.mean():.2f}% — _SLIP_TIERS 재검토",
                          key=f"exec_gap_{pd.Timestamp.now():%Y%m}")
        except Exception as exc:  # noqa: BLE001
            print("alert failed:", exc, file=sys.stderr)
    print("upserted", len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main("--dry-run" in sys.argv))
