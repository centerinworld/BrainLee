#!/usr/bin/env python3
"""Extract the selected (strategy_center) run-set equity curves per period to parquet for the research venv."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402

conn = connect_primary_db(timeout=600)
rows = conn.execute("""SELECT s.strategy, m.period_label, sp.run_id, b.start_date, b.end_date, e.date, e.equity, e.source
  FROM selected_run_registry s JOIN backtest_run_set_members m ON m.suite_hash=s.run_hash
  JOIN backtest_run_specs sp ON sp.run_hash=m.run_hash JOIN backtest_runs b ON b.run_id=sp.run_id
  JOIN backtest_equity_curve e ON e.run_id=sp.run_id WHERE s.report_type='strategy_center' ORDER BY 1,4,6""").fetchall()
df = pd.DataFrame([tuple(r) for r in rows], columns=["strategy", "period", "run_id", "start", "end", "date", "equity", "source"])
out = ROOT / "data_cache" / "research"; out.mkdir(parents=True, exist_ok=True)
df.to_parquet(out / "strategy_period_equity.parquet")
print(len(df), df.strategy.nunique(), df.groupby("strategy").period.nunique().describe()[["min", "max"]].to_dict(),
      df.drop_duplicates("run_id").source.value_counts().to_dict())
