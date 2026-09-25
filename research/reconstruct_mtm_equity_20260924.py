#!/usr/bin/env python3
"""Mark-to-market equity reconstruction for runs that only have closed-trade logs (prod venv).

The 'realized_pnl' curves in backtest_equity_curve are step functions (no value while positions are open), which
understate volatility and distort Sortino/beta in QuantStats. This rebuilds a daily curve:
    equity(d) = initial + realized P&L of trades exited on/before d + sum over open trades of size * (P_d / P_entry - 1)
size = qty*entry_price when qty is logged, else |pnl| / |return| (falls back to per_stock, default 10M). Prices: jump-
adjusted close (research adj_close.parquet) where available, else raw price_history. Trades exiting after the run end
stay open through the last date. Rows are stored with source='mtm_reconstructed' (replacing that run's step curve).
Default scope: runs of the 26 selected strategy_center run-sets; --all for every reconstructed run.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402

K = {"code": ("stock_code", "code", "sc"), "entry": ("entry_date", "buy_date", "entry"), "exit": ("exit_date", "sell_date", "exit"),
     "ep": ("entry_price",), "ret": ("profit_pct", "pnl_pct", "return_pct"), "pnl": ("profit_amt", "pnl"), "qty": ("qty",)}


def _is_date(v):
    return isinstance(v, str) and len(v) >= 10 and v[4] == "-"


def pick(t, key):
    for k in K[key]:
        v = t.get(k)
        if v is None or v == "":
            continue
        if key in ("entry", "exit") and not _is_date(v):
            continue  # some schemas store the PRICE under 'entry'/'exit'
        return v
    if key == "ep":  # schema with entry/exit prices stored as 'entry'/'exit' next to buy_date/sell_date
        v = t.get("entry")
        return v if isinstance(v, (int, float)) else None
    return None


def main(all_runs: bool) -> None:
    conn = connect_primary_db(timeout=900)
    adj = pd.read_parquet(ROOT / "data_cache" / "research" / "adj_close.parquet").astype(float)
    where = "" if all_runs else "AND b.run_id IN (SELECT sp.run_id FROM selected_run_registry s JOIN backtest_run_set_members m ON m.suite_hash=s.run_hash JOIN backtest_run_specs sp ON sp.run_hash=m.run_hash WHERE s.report_type='strategy_center')"
    runs = conn.execute(f"""SELECT b.run_id,b.start_date,b.end_date,b.per_stock,b.max_pos,b.trades_json FROM backtest_runs b
        WHERE b.status='done' AND b.trades_json IS NOT NULL AND length(b.trades_json)>2
          AND b.run_id IN (SELECT run_id FROM backtest_equity_curve WHERE source LIKE 'realized_pnl%' OR source='mtm_reconstructed') {where}""").fetchall()
    stats, out_rows, raw_cache = defaultdict(int), [], {}
    for run_id, start, end, per_stock, max_pos, tj in runs:
        j = json.loads(tj)
        trades = j.get("trades", []) if isinstance(j, dict) else j
        tr = []
        for t in trades:
            code, en, ex = pick(t, "code"), pick(t, "entry"), pick(t, "exit")
            ep = pick(t, "ep"); pnl = pick(t, "pnl"); ret = pick(t, "ret"); qty = pick(t, "qty")
            if not (code and en and ep and pnl is not None):
                continue
            size = float(qty) * float(ep) if qty else (abs(float(pnl)) / abs(float(ret) / 100) if ret not in (None, 0) and abs(float(ret)) > 0.01 else float(per_stock or 1e7))
            tr.append((str(code), str(en)[:10], str(ex)[:10] if ex else None, float(ep), float(pnl), size))
        if not tr:
            stats["no_usable_trades"] += 1
            continue
        s0, e0 = str(start)[:10], str(end)[:10]
        days = pd.bdate_range(s0, e0)
        init = float(per_stock or 0) * float(max_pos or 0) or 1e8
        need = sorted({c for c, *_ in tr})
        miss = [c for c in need if c not in adj.columns and c not in raw_cache]
        if miss:
            for c, d, cl in conn.execute("SELECT stock_code,date,close FROM price_history WHERE stock_code = ANY(?) AND date>='2019-06-01' AND close>0", (miss,)).fetchall():
                raw_cache.setdefault(c, {})[pd.Timestamp(d)] = float(cl)
        def px(code):
            if code in adj.columns:
                return adj[code].dropna()
            return pd.Series(raw_cache.get(code, {})).sort_index()
        px_cache = {c: px(c).reindex(days.union(px(c).index)).ffill().reindex(days) for c in need}
        eq = np.full(len(days), init)
        for code, en, ex, ep, pnl, size in tr:
            p = px_cache[code]
            ent = pd.Timestamp(en); exd = pd.Timestamp(ex) if ex else None
            live = (days >= ent) & ((days < exd) if exd is not None else True)
            i0 = days.searchsorted(ent)
            base = p.iloc[i0] if i0 < len(days) else np.nan
            # ratio against the SAME price series at the entry date: the logged (as-traded) entry price would be on a
            # different level than the jump-adjusted series whenever a split sits between the series anchor and entry
            entry_px = base if base == base and base > 0 else ep
            unreal = (p.values / entry_px - 1) * size
            eq += np.where(live & ~np.isnan(unreal), unreal, 0.0)
            if exd is not None:
                eq += np.where(days >= exd, pnl, 0.0)
        out_rows += [(run_id, d.strftime("%Y-%m-%d"), float(v), "mtm_reconstructed") for d, v in zip(days, eq)]
        stats["mtm_runs"] += 1
    ids = sorted({r[0] for r in out_rows})
    for i in range(0, len(ids), 200):
        conn.execute("DELETE FROM backtest_equity_curve WHERE run_id = ANY(?)", (ids[i:i + 200],))
    conn.executemany("INSERT INTO backtest_equity_curve(run_id,date,equity,source) VALUES(?,?,?,?)", out_rows)
    conn.commit()
    print(dict(stats), "rows", len(out_rows))


if __name__ == "__main__":
    main("--all" in sys.argv)
