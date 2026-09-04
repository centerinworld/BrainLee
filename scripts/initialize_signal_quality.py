#!/usr/bin/env python3
import json,sqlite3,sys
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from signal_quality import score_signal
DDL='''CREATE TABLE IF NOT EXISTS signal_quality_scores(
 signal_id TEXT PRIMARY KEY,stock_code TEXT,signal_date TEXT,signal_type TEXT,strategy_family TEXT,
 market_regime TEXT,quality_score REAL,confidence_score REAL,action TEXT,expected_return_pct REAL,
 downside_pct REAL,positive_rate_pct REAL,sample_count INTEGER,data_quality REAL,score_detail_json TEXT,
 available_at TEXT,expires_at TEXT,created_at TEXT NOT NULL);'''
def main():
 c=sqlite3.connect(ROOT/'stock.db');c.execute(DDL);s=json.loads((ROOT/'research_outputs/deep_drawdown_recovery_5y/summary.json').read_text())
 q=score_signal(expected_return_pct=s['median_returns_from_trigger_pct']['252'],downside_pct=s['median_additional_loss_after_trigger_pct'],positive_rate_pct=s['positive_252d_rate_pct'],sample_count=s['observed_252d_events'],regime_suitability=-.7,data_quality=.9)
 now=datetime.now().isoformat(timespec='seconds');c.execute('INSERT OR REPLACE INTO signal_quality_scores VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('research:deep_drawdown',None,'2026-07-12','deep_drawdown','deep_value_recovery','high_volatility',q['quality_score'],q['confidence_score'],q['action'],q['expected_return_pct'],q['downside_pct'],q['positive_rate_pct'],q['sample_count'],q['data_quality'],json.dumps(q,ensure_ascii=False),'2026-07-12',None,now));c.commit();c.close();print(q)
if __name__=='__main__':main()
