#!/usr/bin/env python3
"""Unify backtest equity-curve storage in table backtest_equity_curve(run_id,date,equity,source).

Why: backtest_runs.equity_json is empty for every run; ~30% of runs carry an engine-produced daily `equity_curve`
inside trades_json, the rest only closed trades (three different trade schemas). QuantStats needs one series per run.
  source='engine'        daily equity_curve stored by the backtest engine (mark-to-market)
  source='realized_pnl_assumed_100m'  same, for runs that did not store per_stock/max_pos (engine default 100M assumed)
  source='realized_pnl'  reconstructed: initial capital (per_stock*max_pos) + cumulative realized P&L of closed trades
                         on their exit dates (no mark-to-market of open positions -> lumpy; flag it in reports)
Existing tables/columns are not modified. Idempotent: rebuilds only runs missing from the table (--rebuild for all).
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402
from price_integrity import native_script  # noqa: E402

DDL = """CREATE TABLE IF NOT EXISTS backtest_equity_curve (
  run_id TEXT NOT NULL, date TEXT NOT NULL, equity DOUBLE PRECISION NOT NULL, source TEXT NOT NULL,
  PRIMARY KEY(run_id, date))"""


def trades_of(j):
    if isinstance(j, dict):
        return j.get("trades") or []
    return j if isinstance(j, list) else []


def exit_date_and_pnl(t: dict):
    d = t.get("exit_date") or t.get("sell_date") or t.get("exit")
    p = t.get("profit_amt")
    if p is None:
        p = t.get("pnl")
    return (str(d)[:10] if d else None), p


def main(rebuild: bool) -> None:
    conn = connect_primary_db(timeout=900)
    native_script(conn, DDL)
    have = set() if rebuild else {r[0] for r in conn.execute("SELECT DISTINCT run_id FROM backtest_equity_curve WHERE source IN ('engine','realized_pnl','realized_pnl_assumed_100m')").fetchall()}
    stats = defaultdict(int)
    rows_out = []
    for run_id, start, end, per_stock, max_pos, tj in conn.execute(
            "SELECT run_id,start_date,end_date,per_stock,max_pos,trades_json FROM backtest_runs "
            "WHERE status='done' AND trades_json IS NOT NULL AND length(trades_json)>2").fetchall():
        if run_id in have:
            continue
        try:
            j = json.loads(tj)
        except Exception:  # noqa: BLE001
            stats["unparseable"] += 1
            continue
        curve = j.get("equity_curve") if isinstance(j, dict) else None
        if curve:
            for p in curve:
                rows_out.append((run_id, str(p["date"])[:10], float(p["equity"]), "engine"))
            stats["engine"] += 1
            continue
        init = float(per_stock or 0) * float(max_pos or 0)
        pnl_by_day = defaultdict(float)
        for t in trades_of(j):
            d, p = exit_date_and_pnl(t)
            if d and p is not None:
                pnl_by_day[d] += float(p)
        src = "realized_pnl"
        if not pnl_by_day:
            stats["skipped_no_realized_pnl"] += 1
            continue
        if not init:  # older runs did not persist per_stock/max_pos; the engine default is 10M x 10 = 100M (see engine curves)
            init, src = 100_000_000.0, "realized_pnl_assumed_100m"

        eq = init
        if start:
            rows_out.append((run_id, str(start)[:10], init, src))
        for d in sorted(pnl_by_day):
            eq += pnl_by_day[d]
            rows_out.append((run_id, d, eq, src))
        if end and str(end)[:10] > max(pnl_by_day):
            rows_out.append((run_id, str(end)[:10], eq, src))
        stats["realized_pnl"] += 1
    import backtest_equity
    backtest_equity.ensure_schema(conn)          # PK (run_id, source, date) + best-source view
    if rebuild:  # only the sources THIS script owns; mtm_reconstructed (reconstruct_mtm_equity_20260924.py) is left alone
        conn.execute("DELETE FROM backtest_equity_curve WHERE source IN ('engine','realized_pnl','realized_pnl_assumed_100m')")
    conn.executemany("INSERT INTO backtest_equity_curve(run_id,date,equity,source) VALUES(?,?,?,?) "
                     "ON CONFLICT (run_id,source,date) DO UPDATE SET equity=excluded.equity", rows_out)
    conn.commit()
    print(dict(stats), "rows", len(rows_out))


if __name__ == "__main__":
    main("--rebuild" in sys.argv)
