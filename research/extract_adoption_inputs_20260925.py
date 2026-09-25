#!/usr/bin/env python3
"""HANDOFF §12 R1 inputs (operating venv, DB read-only) -> data_cache/research/*.parquet for the research venv.

  adoption_runs.parquet     strategy_center selected runs: strategy, period, run_id, start, end, total_trades, equity source
  adoption_trades.parquet   closed trades inside those runs (engine trade logs, cost-inclusive by the engine)
  virtual_trades.parquet    closed paper-trading positions (peak_holding, is_active=0)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

OUT = ROOT / "data_cache" / "research"
OUT.mkdir(parents=True, exist_ok=True)
conn = connect_primary_db(readonly=True, timeout=600)

runs, trades = [], []
for strat, period, run_id, start, end, total, tj in conn.execute(
        """SELECT s.strategy, m.period_label, sp.run_id, b.start_date, b.end_date, b.total_trades, b.trades_json
           FROM selected_run_registry s JOIN backtest_run_set_members m ON m.suite_hash=s.run_hash
           JOIN backtest_run_specs sp ON sp.run_hash=m.run_hash JOIN backtest_runs b ON b.run_id=sp.run_id
           WHERE s.report_type='strategy_center' ORDER BY 1,4""").fetchall():
    runs.append((strat, period, run_id, str(start)[:10], str(end)[:10], total))
    try:
        parsed = json.loads(tj) if tj else {}
        for t in (parsed if isinstance(parsed, list) else parsed.get("trades", [])) or []:   # older runs store the bare trade list
            pct = next((t[k] for k in ("profit_pct", "pnl_pct", "return_pct") if t.get(k) is not None), None)
            trades.append((strat, period, run_id, t.get("stock_code") or t.get("code") or t.get("sc"),
                           str(t.get("entry_date") or t.get("buy_date") or "")[:10], str(t.get("exit_date") or t.get("sell_date") or "")[:10],
                           t.get("entry_price"), pct, t.get("exit_reason")))
    except Exception as exc:  # noqa: BLE001
        print("trades_json parse failed", run_id, exc)
src = {r[0]: r[1] for r in conn.execute(
    "SELECT DISTINCT run_id, source FROM backtest_equity_curve_best_v").fetchall()}
pd.DataFrame(runs, columns=["strategy", "period", "run_id", "start", "end", "total_trades"]).assign(
    source=lambda d: d.run_id.map(src)).to_parquet(OUT / "adoption_runs.parquet")
pd.DataFrame(trades, columns=["strategy", "period", "run_id", "stock_code", "entry_date", "exit_date", "entry_price",
                              "profit_pct", "exit_reason"]).to_parquet(OUT / "adoption_trades.parquet")

vt = conn.execute(
    """SELECT strategy, stock_code, CAST(entry_date AS TEXT), CAST(sold_at AS TEXT), buy_price,
              COALESCE(NULLIF(sold_price,0), NULLIF(sell_price,0)), profit_pct, quantity
       FROM peak_holding WHERE is_active=0""").fetchall()
mc = {r[0]: r[1] for r in conn.execute(
    "SELECT DISTINCT ON (stock_code) stock_code, market_cap FROM stock_universe ORDER BY stock_code, base_date DESC").fetchall()}
v = pd.DataFrame([tuple(r) for r in vt], columns=["strategy", "stock_code", "entry_date", "sold_at", "buy_price", "sell_price", "profit_pct", "qty"])
v["mcap_억"] = v.stock_code.map(mc)
v.to_parquet(OUT / "virtual_trades.parquet")
print(len(runs), len(trades), len(v), "mcap missing:", int(v.mcap_억.isna().sum()))
