#!/usr/bin/env python3
from __future__ import annotations
import csv, json, re, sqlite3, time
from io import StringIO
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup

DB=Path('/Applications/stock_dashboard/stock.db')
OUT=Path('/Applications/stock_dashboard/scratch'); OUT.mkdir(exist_ok=True)
H={'User-Agent':'Mozilla/5.0','Referer':'https://comp.fnguide.com/'}
HN={'User-Agent':'Mozilla/5.0','Referer':'https://finance.naver.com/'}
S=requests.Session()
SN=requests.Session()


def pnum(x)->Optional[float]:
    s=str(x).replace(',','').strip()
    if s in ('','-','N/A','nan','NaN'): return None
    try:return float(s)
    except:return None

def match(a,b,pct,ab):
    if a is None or b is None: return None
    d=abs(a-b)
    if d<=ab:return True
    m=max(abs(a),abs(b),1e-12)
    return d/m<=pct

def targets(conn):
    q="""
    WITH latest AS (
      SELECT stock_code, MAX(base_date) md FROM stock_universe
      WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
      GROUP BY stock_code
    ), u AS (
      SELECT su.stock_code,su.stock_name,su.market,su.market_cap,su.per db_per,su.pbr db_pbr,su.eps db_eps
      FROM stock_universe su JOIN latest l ON su.stock_code=l.stock_code AND su.base_date=l.md
      WHERE COALESCE(su.stock_type,'보통주')='보통주'
        AND COALESCE(su.stock_name,'') NOT LIKE '%ETF%'
        AND COALESCE(su.stock_name,'') NOT LIKE '%ETN%'
    ), k AS (
      SELECT 'KOSPI' mkt_grp,* FROM u WHERE market IN ('유가증권','코스피','KOSPI') ORDER BY market_cap DESC LIMIT 300
    ), qd AS (
      SELECT 'KOSDAQ' mkt_grp,* FROM u WHERE market IN ('코스닥','KOSDAQ') ORDER BY market_cap DESC LIMIT 300
    )
    SELECT * FROM k UNION ALL SELECT * FROM qd
    """
    return conn.execute(q).fetchall()

def latest_fin(conn,code):
    return conn.execute("""SELECT year,revenue,operating_profit,net_income,eps,bps
    FROM financial_data WHERE stock_code=? AND is_annual=1
    ORDER BY year DESC, CASE WHEN data_source='fnguide' THEN 0 ELSE 1 END, id DESC LIMIT 1""",(code,)).fetchone()

def fg_fs(code):
    url=f"https://comp.fnguide.com/SVO2/ASP/SVD_Finance.asp?pGB=1&gicode=A{code}&cID=&MenuYn=Y&ReportGB=&NewMenuID=103&stkGb=701"
    try:
        r=S.get(url,headers=H,timeout=6)
        if r.status_code!=200:return {}
        tables=pd.read_html(StringIO(r.text))
    except Exception:
        return {}
    out={}
    for t in tables:
        if t.empty or t.shape[1]<2: continue
        t=t.copy(); t.columns=[" ".join(str(c).split()) for c in t.columns]
        labels=t.iloc[:,0].astype(str).str.replace(' ','',regex=False)
        years={}
        for c in t.columns[1:]:
            m=re.search(r'(20\d{2})/12',str(c))
            if m: years[int(m.group(1))]=c
        if not years: continue
        def row(keys,ex=()):
            m=labels.str.contains('|'.join(map(re.escape,[k.replace(' ','') for k in keys])),na=False)
            for e in ex: m=m & ~labels.str.contains(re.escape(e.replace(' ','')),na=False)
            rs=t.loc[m]
            return rs.iloc[0] if not rs.empty else None
        rev=row(['매출액','영업수익','매출'])
        op=row(['영업이익'])
        ni=row(['지배주주순이익','당기순이익'],['비지배'])
        for y,c in years.items():
            d=out.setdefault(y,{})
            if rev is not None and 'revenue' not in d:
                v=pnum(rev[c]); d['revenue']=v*1e8 if v is not None else None
            if op is not None and 'operating_profit' not in d:
                v=pnum(op[c]); d['operating_profit']=v*1e8 if v is not None else None
            if ni is not None and 'net_income' not in d:
                v=pnum(ni[c]); d['net_income']=v*1e8 if v is not None else None
    return out

def fg_eps_bps(code):
    url=f"https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp?pGB=1&gicode=A{code}&cID=&MenuYn=Y&ReportGB=&NewMenuID=101&stkGb=701"
    try:
        r=S.get(url,headers=H,timeout=6)
        if r.status_code!=200:return {}
        tables=pd.read_html(StringIO(r.text))
    except Exception:
        return {}
    out={}
    for t in tables:
        if t.empty or t.shape[1]<2: continue
        t=t.copy(); t.columns=[" ".join(str(c).split()) for c in t.columns]
        labels=t.iloc[:,0].astype(str)
        em=labels.str.contains('EPS',na=False); bm=labels.str.contains('BPS',na=False)
        if not em.any() and not bm.any(): continue
        years={}
        for c in t.columns[1:]:
            m=re.search(r'(20\d{2})/12',str(c))
            if m and '(E)' not in str(c): years[int(m.group(1))]=c
        for y,c in years.items():
            d=out.setdefault(y,{})
            if em.any() and 'eps' not in d: d['eps']=pnum(t.loc[em].iloc[0][c])
            if bm.any() and 'bps' not in d: d['bps']=pnum(t.loc[bm].iloc[0][c])
    return out

def naver(code):
    try:
        r=SN.get(f'https://finance.naver.com/item/main.naver?code={code}',headers=HN,timeout=6)
        r.encoding='euc-kr'; s=BeautifulSoup(r.text,'html.parser')
        def g(sel):
            n=s.select_one(sel); return pnum(n.text if n else '')
        return {'per':g('em#_per'),'pbr':g('em#_pbr'),'eps':g('em#_eps')}
    except Exception:
        return {'per':None,'pbr':None,'eps':None}

def main():
    conn=sqlite3.connect(DB); conn.row_factory=sqlite3.Row
    tars=targets(conn)
    res=[]
    for i,r in enumerate(tars,1):
        code=r['stock_code']; fin=latest_fin(conn,code); year=int(fin['year']) if fin else None
        f1=fg_fs(code); f2=fg_eps_bps(code); nv=naver(code)
        fy=f1.get(year,{}) if year else {}; ey=f2.get(year,{}) if year else {}
        row={
          'market':r['mkt_grp'],'stock_code':code,'stock_name':r['stock_name'],'year':year,
          'ok_revenue':match(fin['revenue'] if fin else None,fy.get('revenue'),0.03,5e8),
          'ok_operating_profit':match(fin['operating_profit'] if fin else None,fy.get('operating_profit'),0.03,3e8),
          'ok_net_income':match(fin['net_income'] if fin else None,fy.get('net_income'),0.04,3e8),
          'ok_eps':match(fin['eps'] if fin else None,ey.get('eps'),0.05,30),
          'ok_bps':match(fin['bps'] if fin else None,ey.get('bps'),0.05,80),
          'ok_per':match(r['db_per'],nv.get('per'),0.06,0.5),
          'ok_pbr':match(r['db_pbr'],nv.get('pbr'),0.06,0.08),
          'ok_eps_naver':match(r['db_eps'],nv.get('eps'),0.06,40),
          'db_per':r['db_per'],'naver_per':nv.get('per'),'db_pbr':r['db_pbr'],'naver_pbr':nv.get('pbr'),
          'db_eps_univ':r['db_eps'],'naver_eps':nv.get('eps')
        }
        res.append(row)
        if i%25==0:
            print(f'progress {i}/600', flush=True)
        time.sleep(0.03)

    fields=['ok_revenue','ok_operating_profit','ok_net_income','ok_eps','ok_bps','ok_per','ok_pbr','ok_eps_naver']
    summ={'total':len(res),'fields':{},'market':{}}
    for f in fields:
        vals=[x[f] for x in res]; cov=sum(v is not None for v in vals); ps=sum(v is True for v in vals); fl=sum(v is False for v in vals)
        summ['fields'][f]={'coverage':cov,'pass':ps,'fail':fl,'pass_rate_pct':round(ps/cov*100,2) if cov else None}
    for m in ['KOSPI','KOSDAQ']:
        mr=[x for x in res if x['market']==m]; d={}
        for f in fields:
            vals=[x[f] for x in mr]; cov=sum(v is not None for v in vals); ps=sum(v is True for v in vals)
            d[f]=round(ps/cov*100,2) if cov else None
        summ['market'][m]={'count':len(mr),'pass_rate_pct':d}

    ts=time.strftime('%Y%m%d_%H%M%S')
    cp=OUT/f'major600_verify_fast_{ts}.csv'; jp=OUT/f'major600_verify_fast_{ts}.json'
    with cp.open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=list(res[0].keys())); w.writeheader(); w.writerows(res)
    jp.write_text(json.dumps({'summary':summ,'rows':res[:200]},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'summary':summ,'csv':str(cp),'json':str(jp)},ensure_ascii=False,indent=2), flush=True)

if __name__=='__main__':
    main()
