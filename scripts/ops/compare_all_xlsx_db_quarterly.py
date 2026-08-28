#!/usr/bin/env python3
import sqlite3
import pandas as pd
import re
from pathlib import Path
from collections import defaultdict

DB='/Applications/stock_dashboard/stock.db'
OUT=Path('/Applications/stock_dashboard/scratch')
OUT.mkdir(parents=True, exist_ok=True)

q_re = re.compile(r'(?:20)?(\d{2})\s*[-./년]?\s*([1-4])\s*[Qq]')

METRIC_PATTERNS = {
    'revenue': ['매출액','매출'],
    'operating_profit': ['영업이익'],
    'net_income': ['순이익','당기순이익'],
    'ocf': ['영업활동현금흐름','영업활동CF','영업CF','OCF'],
    'icf': ['투자활동현금흐름','투자활동CF','투자CF','ICF'],
    'fcf_fin': ['재무활동현금흐름','재무활동CF','재무CF'],
    'capex': ['CAPEX','CapEx','설비투자'],
    'depreciation': ['감가상각비','감가상각']
}

def norm_quarter(s):
    if s is None:
        return None
    t = str(s).strip().replace(' ', '')
    m = q_re.search(t)
    if not m:
        return None
    yy = int(m.group(1))
    y = 2000 + yy if yy < 100 else yy
    q = int(m.group(2))
    return f"{y}Q{q}"

def detect_unit(df):
    txt = ' '.join(str(x) for x in df.fillna('').values.flatten()[:12000])
    if '백만원' in txt:
        return 'million'
    if '천원' in txt:
        return 'thousand'
    if '(억원)' in txt or '단위: 억' in txt or ' 억' in txt:
        return 'eok'
    return 'unknown'

def to_eok(v, unit):
    if v is None:
        return None
    if unit == 'million':
        return float(v) / 100.0
    if unit == 'thousand':
        return float(v) / 100000.0
    return float(v)

def parse_number(x):
    if x is None:
        return None
    s = str(x).strip().replace(',','')
    if s in ('','-','--','nan','None'):
        return None
    neg = False
    if s.startswith('(') and s.endswith(')'):
        neg=True
        s=s[1:-1]
    s=s.replace('억','').replace('원','').replace('%','')
    try:
        v=float(s)
        return -v if neg else v
    except:
        return None

def metric_from_label(lbl):
    t = str(lbl).strip()
    for k, pats in METRIC_PATTERNS.items():
        for p in pats:
            if p in t:
                return k
    return None

def parse_sheet_row_oriented(df):
    """rows=metrics, cols=quarters"""
    out = defaultdict(dict)
    # find quarter-rich header rows
    for i in range(min(len(df), 180)):
        row = [str(x).strip() for x in df.iloc[i].tolist()]
        qcols = {}
        for j,v in enumerate(row):
            nq = norm_quarter(v)
            if nq:
                qcols[j]=nq
        if len(qcols) < 3:
            continue

        # assume metric labels appear in first 2~3 cols below
        for r in range(i+1, min(i+140, len(df))):
            found=False
            for label_col in [0,1,2]:
                if label_col >= df.shape[1]:
                    continue
                m = metric_from_label(df.iat[r,label_col])
                if not m:
                    continue
                found=True
                for c,q in qcols.items():
                    if c >= df.shape[1]:
                        continue
                    v = parse_number(df.iat[r,c])
                    if v is None:
                        continue
                    out[q][m]=v
            if not found and r>i+40:
                break
    return out

def parse_sheet_col_oriented(df):
    """rows=quarters, cols=metrics"""
    out = defaultdict(dict)
    # locate metric header columns
    header_candidates=[]
    for i in range(min(len(df), 120)):
        row=[str(x).strip() for x in df.iloc[i].tolist()]
        metric_cols={}
        for j,v in enumerate(row):
            m=metric_from_label(v)
            if m:
                metric_cols[j]=m
        if len(metric_cols)>=3:
            header_candidates.append((i,metric_cols))

    for i,metric_cols in header_candidates:
        # quarter likely in first few cols
        for r in range(i+1, min(i+180, len(df))):
            q=None
            q_col=None
            for c in [0,1,2,3]:
                if c>=df.shape[1]:
                    continue
                q=norm_quarter(df.iat[r,c])
                if q:
                    q_col=c
                    break
            if not q:
                continue
            for c,m in metric_cols.items():
                if c>=df.shape[1]:
                    continue
                v=parse_number(df.iat[r,c])
                if v is None:
                    continue
                out[q][m]=v
    return out

def merge_dict(base, more):
    for q,vals in more.items():
        for k,v in vals.items():
            # keep first non-null unless missing
            if k not in base[q] or base[q][k] is None:
                base[q][k]=v


def parse_excel(path):
    xl=pd.ExcelFile(path)
    collected=defaultdict(dict)
    used_sheets=[]
    for sh in xl.sheet_names:
        try:
            df=xl.parse(sh, header=None)
        except Exception:
            continue
        unit=detect_unit(df)
        r=parse_sheet_row_oriented(df)
        c=parse_sheet_col_oriented(df)
        # convert unit
        for d in (r,c):
            for q,vals in d.items():
                for k,v in list(vals.items()):
                    vals[k]=to_eok(v, unit)
        before=sum(len(v) for v in collected.values())
        merge_dict(collected, r)
        merge_dict(collected, c)
        after=sum(len(v) for v in collected.values())
        if after>before:
            used_sheets.append(sh)
    return dict(collected), used_sheets


def db_map(conn, stock_code):
    f=pd.read_sql_query("""
      SELECT year,quarter,revenue,operating_profit,net_income,data_source
      FROM financial_data
      WHERE stock_code=? AND is_annual=0 AND report_type='CFS'
      ORDER BY year,quarter,id
    """, conn, params=[stock_code])
    c=pd.read_sql_query("""
      SELECT year,quarter,operating_cf_q,investing_cf_q,financing_cf_q,capex_q,depreciation_q,depreciation,data_source,value_type
      FROM cash_flow_data
      WHERE stock_code=? AND is_annual=0 AND report_type='CFS'
      ORDER BY year,quarter,id
    """, conn, params=[stock_code])
    out=defaultdict(dict)
    for _,r in f.iterrows():
      q=f"{int(r.year)}Q{int(r.quarter)}"
      out[q]['revenue']= (r.revenue/1e8 if pd.notna(r.revenue) else None)
      out[q]['operating_profit']= (r.operating_profit/1e8 if pd.notna(r.operating_profit) else None)
      out[q]['net_income']= (r.net_income/1e8 if pd.notna(r.net_income) else None)
      out[q]['fin_src']=r.data_source
    for _,r in c.iterrows():
      q=f"{int(r.year)}Q{int(r.quarter)}"
      if pd.notna(r.operating_cf_q): out[q]['ocf']=r.operating_cf_q/1e8
      if pd.notna(r.investing_cf_q): out[q]['icf']=r.investing_cf_q/1e8
      if pd.notna(r.financing_cf_q): out[q]['fcf_fin']=r.financing_cf_q/1e8
      if pd.notna(r.capex_q): out[q]['capex']=r.capex_q/1e8
      if pd.notna(r.depreciation_q): out[q]['depreciation']=r.depreciation_q/1e8
      elif pd.notna(r.depreciation): out[q]['depreciation']=r.depreciation/1e8
      if pd.notna(r.data_source): out[q]['cf_src']=r.data_source
      if pd.notna(r.value_type): out[q]['cf_vtype']=r.value_type
    return dict(out)


def main():
    conn=sqlite3.connect(DB)
    conn.row_factory=sqlite3.Row
    rows=conn.execute("""
      SELECT p.stock_code,p.stock_name,f.file_name,f.file_path
      FROM detailed_analysis_files f
      JOIN detailed_analysis_posts p ON p.id=f.post_id
      WHERE (lower(f.file_type)='xlsx' OR lower(f.file_name) LIKE '%.xlsx%')
        AND f.file_path IS NOT NULL
    """).fetchall()

    # unique by actual path
    uniq=[]
    seen=set()
    for r in rows:
      p=str(r['file_path']).strip()
      if not p or p in seen:
        continue
      seen.add(p)
      uniq.append((str(r['stock_code']).zfill(6), r['stock_name'], r['file_name'], p))

    sum_rows=[]
    det_rows=[]
    for sc,sn,fn,p in uniq:
      fp=Path(p)
      if not fp.exists():
        sum_rows.append([sc,sn,fn,p,'MISSING_FILE',0,0,0,0,0,''])
        continue
      try:
        ex, used = parse_excel(fp)
      except Exception as e:
        sum_rows.append([sc,sn,fn,p,'PARSE_FAIL',0,0,0,0,0,str(e)])
        continue
      dbm=db_map(conn, sc)
      common=sorted(set(ex.keys()) & set(dbm.keys()))
      compare_fields=['revenue','operating_profit','net_income','ocf','icf','fcf_fin','capex','depreciation']
      mismatch=0
      total=0
      for q in common:
        for fld in compare_fields:
          ev=ex.get(q,{}).get(fld)
          dv=dbm.get(q,{}).get(fld)
          if ev is None or dv is None:
            continue
          total += 1
          tol=max(2.0, abs(ev)*0.15)
          diff=dv-ev
          ok=abs(diff)<=tol
          if not ok:
            mismatch += 1
          det_rows.append([sc,sn,fn,p,q,fld,ev,dv,diff,tol,'OK' if ok else 'DIFF',dbm.get(q,{}).get('fin_src'),dbm.get(q,{}).get('cf_src'),dbm.get(q,{}).get('cf_vtype')])

      status='OK'
      if len(common)==0:
        status='NO_COMMON_Q'
      elif total==0:
        status='NO_COMPARABLE_FIELDS'
      elif mismatch>0:
        status='HIGH_MISMATCH' if mismatch>=max(3,int(total*0.4)) else 'SOME_MISMATCH'

      sum_rows.append([sc,sn,fn,p,status,len(ex),len(dbm),len(common),mismatch,total,'|'.join(used[:8])])

    ts=pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')
    sum_df=pd.DataFrame(sum_rows, columns=['stock_code','stock_name','file_name','file_path','status','excel_q_cnt','db_q_cnt','common_q_cnt','mismatch_cnt','compared_cnt','used_sheets'])
    det_df=pd.DataFrame(det_rows, columns=['stock_code','stock_name','file_name','file_path','quarter','field','excel_value_eok','db_value_eok','diff_eok','tolerance_eok','judge','fin_src','cf_src','cf_vtype'])

    sum_csv=OUT/f'xlsx_db_compare_summary_{ts}.csv'
    det_csv=OUT/f'xlsx_db_compare_detail_{ts}.csv'
    sum_df.to_csv(sum_csv,index=False,encoding='utf-8-sig')
    det_df.to_csv(det_csv,index=False,encoding='utf-8-sig')

    print('summary_csv=',sum_csv)
    print('detail_csv=',det_csv)
    print('\nstatus counts:')
    print(sum_df['status'].value_counts(dropna=False).to_string())
    print('\nNO_COMMON sample:')
    print(sum_df[sum_df.status=='NO_COMMON_Q'][['stock_code','stock_name','file_name']].head(20).to_string(index=False))

    conn.close()

if __name__=='__main__':
    main()
