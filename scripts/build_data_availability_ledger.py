#!/usr/bin/env python3
"""Build a unified availability ledger without rewriting source tables."""
from __future__ import annotations

import sqlite3
import sys
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "stock.db"
sys.path.insert(0, str(ROOT))

from point_in_time import next_weekday

DDL = """
CREATE TABLE IF NOT EXISTS data_availability_ledger (
  dataset TEXT NOT NULL,
  entity_key TEXT NOT NULL,
  period_key TEXT NOT NULL,
  available_at TEXT NOT NULL,
  availability_quality TEXT NOT NULL,
  source_reference TEXT,
  rule_version TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(dataset,entity_key,period_key)
);
CREATE INDEX IF NOT EXISTS idx_dal_available ON data_availability_ledger(dataset,available_at,entity_key);
CREATE TABLE IF NOT EXISTS strict_backtest_runs (
  run_id TEXT PRIMARY KEY,
  strategy_id TEXT NOT NULL,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  signal_date_min TEXT,
  signal_date_max TEXT,
  decision_time TEXT NOT NULL,
  execution_price_type TEXT NOT NULL,
  price_basis TEXT NOT NULL,
  transaction_cost_bps REAL NOT NULL DEFAULT 0,
  slippage_bps REAL NOT NULL DEFAULT 0,
  lookahead_violations INTEGER NOT NULL DEFAULT 0,
  availability_fallback_rows INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  result_json TEXT
);
"""


def table_exists(conn, name: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def main() -> None:
    conn = sqlite3.connect(DB, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)
    now = datetime.now().isoformat(timespec="seconds")
    rows = []
    if table_exists(conn, "fin_disclosure_dates"):
        for r in conn.execute("SELECT stock_code,year,quarter,is_annual,avail_date,disclosure_date FROM fin_disclosure_dates WHERE avail_date IS NOT NULL"):
            rows.append(("financial",r["stock_code"],f"{r['year']}Q{r['quarter']}:{'annual' if r['is_annual'] else 'quarter'}",r["avail_date"],"exact_disclosure",r["disclosure_date"],"pit-v1",now))
    for table, dataset in (("dart_cost_quarterly","material_cost"),("dart_backlog_quarterly","order_backlog")):
        if not table_exists(conn, table):
            continue
        cols={r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "source_rcept_dt" not in cols:
            continue
        for r in conn.execute(f"SELECT stock_code,fiscal_year year,fiscal_quarter quarter,source_rcept_dt FROM {table} WHERE source_rcept_dt IS NOT NULL"):
            available=next_weekday(r["source_rcept_dt"])
            if available:
                rows.append((dataset,r["stock_code"],f"{r['year']}Q{r['quarter']}",available,"exact_disclosure",r["source_rcept_dt"],"pit-v1",now))
    if table_exists(conn,"quant_major_indicator_series"):
        cols={r[1] for r in conn.execute("PRAGMA table_info(quant_major_indicator_series)")}
        key_col="indicator_key" if "indicator_key" in cols else "series_key"
        period_col="period" if "period" in cols else "date"
        for r in conn.execute(f"SELECT {key_col} k,{period_col} p,series_name,source_name,updated_at FROM quant_major_indicator_series WHERE {period_col} IS NOT NULL"):
            p=str(r["p"])
            match=re.match(r"^(\d{4})-?(\d{2})",p)
            year=int(match.group(1)) if match else None
            month=int(match.group(2)) if match else None
            if not year or not month or not 1<=month<=12: continue
            next_month = f"{year + (month==12):04d}-{1 if month==12 else month+1:02d}-15"
            entity=f"{r['k']}|{r['series_name']}|{r['source_name']}"
            rows.append(("quant_indicator",entity,p,next_month,"fallback_lag",str(r["updated_at"] or ""),"pit-v1",now))
    conn.execute("DELETE FROM data_availability_ledger")
    conn.executemany("INSERT INTO data_availability_ledger VALUES(?,?,?,?,?,?,?,?)",rows)
    conn.commit()
    print({"rows":len(rows),"exact":sum(r[4]=="exact_disclosure" for r in rows),"fallback":sum(r[4]=="fallback_lag" for r in rows)})
    conn.close()

if __name__ == "__main__": main()
