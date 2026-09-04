#!/usr/bin/env python3
import json,sqlite3
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];DB=ROOT/'stock.db'
DDL='''CREATE TABLE IF NOT EXISTS data_lineage_catalog(
 metric_key TEXT PRIMARY KEY,display_name TEXT NOT NULL,source_name TEXT NOT NULL,source_table TEXT NOT NULL,
 period_field TEXT,available_at_rule TEXT NOT NULL,formula TEXT NOT NULL,value_type TEXT NOT NULL,
 quality_rule TEXT NOT NULL,collector TEXT,owner TEXT NOT NULL,updated_at TEXT NOT NULL);'''
ROWS=[
('price.close','종가','KIS/Naver/KRX','canonical_price_history_v','date','거래일 장마감 후 다음 영업일 사용','selected_series.close','raw/adjusted','return_usable=1','collect_kis_ohlcv.py','market-data'),
('financial.revenue','매출','DART/FnGuide','financial_data','year,quarter','fin_disclosure_dates.avail_date','reported revenue','reported','exact disclosure preferred','financial collectors','fundamentals'),
('financial.forward_per','Forward PER','KIS consensus','consensus_estimates','period','provider collection timestamp','price / forecast EPS','estimate','estimate flag and source required','consensus collector','fundamentals'),
('indicator.value','퀀트 지표','ECOS/KAMA/KRX/customs','quant_major_indicator_series','period','data_availability_ledger','source value or documented derived formula','raw/derived','quality column + fallback status','sync_quant_major_indicators.py','quant'),
('stock.indicator.exposure','지표 사업노출','DART reports/cafe research','cafe_stock_indicator_mappings','updated_at','mapping evidence date','max(revenue,profit,cost exposure)','estimate','0<=exposure<=100 and confidence','mapping pipelines','quant'),
('signal.quality','신호 품질','Internal research','signal_quality_scores','signal_date','max input available_at','edge+hit+regime-downside-penalties','derived','lookahead=0 and sample confidence','signal_quality.py','strategy'),
('market.regime','시장 국면','KOSPI canonical price','market_regime_daily','trade_date','next business day','MA200+60d return+60d volatility','derived','index continuity audit','build_market_regime_history.py','strategy'),
('corporate.action','자본행위','DART+listed shares','corporate_action_events','event_date','event/effective date','old shares/new shares factor','reported/derived','factor only when type+direction agree','build_corporate_action_adjustment_engine.py','market-data'),
]
def main():
 c=sqlite3.connect(DB);c.execute(DDL);now=datetime.now().isoformat(timespec='seconds');c.executemany('INSERT OR REPLACE INTO data_lineage_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',[r+(now,) for r in ROWS]);c.commit();print({'metrics':len(ROWS)});c.close()
if __name__=='__main__':main()
