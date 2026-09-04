#!/usr/bin/env python3
"""Build point-in-time market regimes from canonical KOSPI history."""
from __future__ import annotations
import sqlite3
from datetime import datetime
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]; DB=ROOT/'stock.db'
DDL="""
CREATE TABLE IF NOT EXISTS market_regime_daily(
 trade_date TEXT PRIMARY KEY,index_code TEXT NOT NULL,close REAL,ma60 REAL,ma200 REAL,
 return_20d REAL,return_60d REAL,volatility_60d REAL,drawdown_252d REAL,
 trend_regime TEXT NOT NULL,volatility_regime TEXT NOT NULL,market_regime TEXT NOT NULL,
 regime_score REAL NOT NULL,available_at TEXT NOT NULL,rule_version TEXT NOT NULL,updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mrd_regime_date ON market_regime_daily(market_regime,trade_date);
CREATE TABLE IF NOT EXISTS strategy_regime_policy(
 strategy_family TEXT NOT NULL,market_regime TEXT NOT NULL,suitability_score REAL NOT NULL,
 action TEXT NOT NULL,rationale TEXT NOT NULL,updated_at TEXT NOT NULL,
 PRIMARY KEY(strategy_family,market_regime)
);
"""
POLICY={
 'momentum':{'bull':(1,'active','추세 지속 우호'),'sideways':(.3,'reduced','휩쏘 주의'),'bear':(-.7,'off','추세 하락 위험'),'high_volatility':(-.4,'reduced','손절·슬리피지 확대')},
 'deep_value_recovery':{'bull':(.4,'reduced','시장 상승 보조'),'sideways':(.2,'reduced','개별 촉매 필요'),'bear':(-.8,'off','낙폭 확대 위험'),'high_volatility':(-.7,'off','저점 오판 위험')},
 'breakout':{'bull':(1,'active','신고가 확산 우호'),'sideways':(.1,'reduced','실패 돌파 증가'),'bear':(-.8,'off','돌파 지속성 약함'),'high_volatility':(-.3,'reduced','가짜 돌파 주의')},
 'mean_reversion':{'bull':(.2,'reduced','추세 역행 주의'),'sideways':(.8,'active','평균회귀 우호'),'bear':(-.4,'reduced','추가 하락 위험'),'high_volatility':(.1,'reduced','짧은 보유만')},
}
def main():
 c=sqlite3.connect(DB); c.executescript(DDL)
 d=pd.read_sql_query("SELECT substr(date,1,10) date,close FROM price_history WHERE stock_code='^KS11' AND close>0 AND date>='2014-01-01' ORDER BY date",c)
 d['date']=pd.to_datetime(d.date); d=d.drop_duplicates('date').set_index('date'); r=d.close.pct_change()
 d['ma60']=d.close.rolling(60,min_periods=40).mean();d['ma200']=d.close.rolling(200,min_periods=120).mean()
 d['r20']=d.close.pct_change(20);d['r60']=d.close.pct_change(60);d['vol60']=r.rolling(60,min_periods=40).std()*np.sqrt(252)
 d['dd252']=d.close/d.close.rolling(252,min_periods=120).max()-1
 now=datetime.now().isoformat(timespec='seconds'); rows=[]
 for dt,x in d[d.index>='2015-01-01'].dropna(subset=['ma200']).iterrows():
  trend='bull' if x.close>x.ma200 and x.r60>0.03 else 'bear' if x.close<x.ma200 and x.r60<-.03 else 'sideways'
  vol='high' if x.vol60>=.30 else 'normal'
  regime='high_volatility' if vol=='high' else trend
  score=(1 if trend=='bull' else -1 if trend=='bear' else 0)+(max(-1,min(1,x.r60/.15)))-(.5 if vol=='high' else 0)
  rows.append((dt.date().isoformat(),'^KS11',x.close,x.ma60,x.ma200,x.r20*100,x.r60*100,x.vol60*100,x.dd252*100,trend,vol,regime,score,(dt.date()+pd.offsets.BDay(1)).date().isoformat(),'regime-v1',now))
 c.executemany("INSERT OR REPLACE INTO market_regime_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",rows)
 policies=[]
 for family,regs in POLICY.items():
  for regime,(score,action,reason) in regs.items():policies.append((family,regime,score,action,reason,now))
 c.executemany("INSERT OR REPLACE INTO strategy_regime_policy VALUES(?,?,?,?,?,?)",policies);c.commit()
 print({'regime_rows':len(rows),'from':rows[0][0] if rows else None,'to':rows[-1][0] if rows else None,'policies':len(policies)})
 c.close()
if __name__=='__main__':main()
