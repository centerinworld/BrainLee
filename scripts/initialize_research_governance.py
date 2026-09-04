#!/usr/bin/env python3
from __future__ import annotations
import json, sqlite3, sys
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from research_governance import validate_research_record

DDL="""
CREATE TABLE IF NOT EXISTS hypothesis_research_registry (
 research_id TEXT PRIMARY KEY,title TEXT NOT NULL,hypothesis TEXT NOT NULL,verdict TEXT NOT NULL,
 status TEXT NOT NULL,latest_run_id TEXT,validation_json TEXT NOT NULL,updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hypothesis_research_runs (
 run_id TEXT PRIMARY KEY,research_id TEXT NOT NULL,record_json TEXT NOT NULL,publishable INTEGER NOT NULL,
 created_at TEXT NOT NULL,FOREIGN KEY(research_id) REFERENCES hypothesis_research_registry(research_id)
);
"""
def main():
 c=sqlite3.connect(ROOT/'stock.db'); c.executescript(DDL); now=datetime.now().isoformat(timespec='seconds')
 s=json.loads((ROOT/'research_outputs/deep_drawdown_recovery_5y/summary.json').read_text())
 rec={"research_id":"deep_drawdown_recovery_5y","title":"낙폭과대·52주 신저가 회복",
 "hypothesis":"고점 대비 60~70% 이상 하락한 종목은 하방이 제한되어 좋은 매수 기회가 된다.","verdict":"rejected",
 "sample_count":s['all_events'],"stock_count":s['all_stocks'],"period_start":"2021-01-01","period_end":"2025-12-31",
 "total_return_pct":None,"cagr_pct":None,"mdd_pct":None,"positive_rate_pct":s['positive_252d_rate_pct'],"profit_factor":None,
 "initial_capital":None,"price_basis":"canonical_research","execution_price_type":"unmodeled_event_return",
 "transaction_cost_bps":None,"slippage_bps":None,"is_out_of_sample":False,"lookahead_violations":0,
 "availability_fallback_rows":0,"survivorship_bias_controlled":True}
 v=validate_research_record(rec); run='deep_drawdown_recovery_5y_20260712'
 c.execute("INSERT OR REPLACE INTO hypothesis_research_registry VALUES(?,?,?,?,?,?,?,?)",(rec['research_id'],rec['title'],rec['hypothesis'],rec['verdict'],v['status'],run,json.dumps(v,ensure_ascii=False),now))
 c.execute("INSERT OR REPLACE INTO hypothesis_research_runs VALUES(?,?,?,?,?)",(run,rec['research_id'],json.dumps(rec,ensure_ascii=False),int(v['publishable']),now)); c.commit(); c.close(); print(v)
if __name__=='__main__': main()
