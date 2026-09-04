#!/usr/bin/env python3
"""Link indicator changes to stocks using verified business exposure."""
from __future__ import annotations
import json,sqlite3
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];DB=ROOT/'stock.db'
DDL='''CREATE TABLE IF NOT EXISTS explainable_stock_signals(
 stock_code TEXT NOT NULL,indicator_key TEXT NOT NULL,period TEXT NOT NULL,indicator_name TEXT,
 latest_value REAL,previous_value REAL,change_pct REAL,direction TEXT NOT NULL,
 revenue_exposure_pct REAL,profit_exposure_pct REAL,cost_exposure_pct REAL,exposure_basis TEXT,
 mapping_confidence REAL,weighted_impact_score REAL,signal_strength TEXT NOT NULL,
 explanation TEXT NOT NULL,available_at TEXT,data_quality TEXT,created_at TEXT NOT NULL,
 PRIMARY KEY(stock_code,indicator_key,period));
CREATE INDEX IF NOT EXISTS idx_ess_stock_period ON explainable_stock_signals(stock_code,period,signal_strength);'''
def main():
 c=sqlite3.connect(DB);c.row_factory=sqlite3.Row;c.executescript(DDL);now=datetime.now().isoformat(timespec='seconds')
 rows=c.execute('''WITH ranked AS (SELECT indicator_key,period,value,quality,ROW_NUMBER() OVER(PARTITION BY indicator_key ORDER BY period DESC,id DESC) rn FROM quant_major_indicator_series WHERE value IS NOT NULL),
 latest AS (SELECT a.indicator_key,a.period,a.value latest_value,b.value previous_value,a.quality FROM ranked a LEFT JOIN ranked b ON b.indicator_key=a.indicator_key AND b.rn=2 WHERE a.rn=1)
 SELECT m.*,l.period,l.latest_value,l.previous_value,l.quality,
   (SELECT MAX(q.available_at) FROM data_availability_ledger q WHERE q.dataset='quant_indicator' AND q.period_key=l.period AND q.entity_key LIKE l.indicator_key||'|%') available_at
 FROM cafe_stock_indicator_mappings m JOIN latest l USING(indicator_key)
 WHERE COALESCE(m.mapping_status,'') NOT IN ('rejected','invalid')''').fetchall()
 out=[]
 for r in rows:
  prev=r['previous_value']; latest=r['latest_value'];chg=(latest/abs(prev)-1)*100 if prev not in (None,0) else None
  direction='positive' if chg is not None and chg>=5 else 'negative' if chg is not None and chg<=-5 else 'neutral'
  raw_exposure=max([x or 0 for x in (r['revenue_exposure_pct'],r['profit_exposure_pct'],r['cost_exposure_pct'])]);exposure=min(100,raw_exposure);conf=float(r['confidence'] or 0)
  invalid_exposure=raw_exposure>100
  if invalid_exposure: conf*=.4
  impact=(chg or 0)*(exposure/100)*conf;strength='low' if invalid_exposure else 'high' if exposure>=30 and conf>=.75 and abs(impact)>=3 else 'medium' if exposure>=10 and conf>=.6 and abs(impact)>=1 else 'low'
  name=r['indicator_name'] or r['indicator_key'];explain=f"{name} {chg:+.1f}% · 사업 노출 {exposure:.1f}% · 매핑 신뢰도 {conf*100:.0f}%" if chg is not None else f"{name} 이전값 부족"
  if invalid_exposure: explain+=f" · 원본 노출 {raw_exposure:.1f}%로 검증 필요"
  out.append((r['stock_code'],r['indicator_key'],r['period'],name,latest,prev,chg,direction,r['revenue_exposure_pct'],r['profit_exposure_pct'],r['cost_exposure_pct'],r['exposure_basis'],conf,impact,strength,explain,r['available_at'],r['quality'],now))
 c.execute('DELETE FROM explainable_stock_signals');c.executemany('INSERT INTO explainable_stock_signals VALUES('+','.join('?'*19)+')',out);c.commit()
 print({'signals':len(out),'stocks':len(set(r[0] for r in out)),'high':sum(r[14]=='high' for r in out),'medium':sum(r[14]=='medium' for r in out)})
 c.close()
if __name__=='__main__':main()
