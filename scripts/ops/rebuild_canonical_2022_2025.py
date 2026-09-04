#!/usr/bin/env python3
import sqlite3
import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_write_gate import ensure_canonical_schema, gate_financial_row, gate_cashflow_row, upsert_canonical_financial, upsert_canonical_cashflow

DB='/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db'
Y1,Y2=2022,2025

def qscore_fin(r):
    s=0
    for k in ['revenue','operating_profit','net_income']:
        if r[k] is not None: s+=1
    for k in ['total_assets','total_liabilities','total_equity']:
        if r[k] is not None: s+=1
    if r['data_source']=='dart': s+=1
    if r['report_type']=='CFS': s+=0.5
    return s

def qscore_cf(r):
    s=0
    for k in ['operating_cf','investing_cf','financing_cf','capex','depreciation','cash_end']:
        if r[k] is not None: s+=1
    for k in ['operating_cf_q','investing_cf_q','financing_cf_q','capex_q']:
        if r[k] is not None: s+=0.7
    if r['data_source']=='dart': s+=1
    return s

def main():
    conn=sqlite3.connect(DB)
    conn.row_factory=sqlite3.Row
    ensure_canonical_schema(conn)

    conn.execute('DELETE FROM canonical_financial_data WHERE year BETWEEN ? AND ?', (Y1,Y2))
    conn.execute('DELETE FROM canonical_cashflow_data WHERE year BETWEEN ? AND ?', (Y1,Y2))

    # financial canonical rebuild: key별 최고점 1행
    keys=conn.execute('''
    SELECT stock_code,year,quarter,is_annual,COALESCE(report_type,'CFS') report_type
    FROM financial_data
    WHERE year BETWEEN ? AND ?
    GROUP BY 1,2,3,4,5
    ''',(Y1,Y2)).fetchall()

    fin_rows=0
    for k in keys:
        rs=conn.execute('''
        SELECT * FROM financial_data
        WHERE stock_code=? AND year=? AND quarter=? AND is_annual=? AND COALESCE(report_type,'CFS')=?
        ORDER BY id DESC
        ''',(k['stock_code'],k['year'],k['quarter'],k['is_annual'],k['report_type'])).fetchall()
        if not rs: continue
        best=max(rs,key=qscore_fin)
        p=dict(best)
        p['report_type']=k['report_type']
        ok,fixed,_=gate_financial_row(conn,p)
        if not ok: continue
        upsert_canonical_financial(conn,fixed,source_row_id=best['id'],decision_reason='rebuild_2022_2025',quality_score=qscore_fin(best))
        fin_rows+=1

    # cashflow canonical rebuild
    keys=conn.execute('''
    SELECT stock_code,year,quarter,is_annual,COALESCE(report_type,'CFS') report_type
    FROM cash_flow_data
    WHERE year BETWEEN ? AND ?
    GROUP BY 1,2,3,4,5
    ''',(Y1,Y2)).fetchall()

    cf_rows=0
    for k in keys:
        rs=conn.execute('''
        SELECT * FROM cash_flow_data
        WHERE stock_code=? AND year=? AND quarter=? AND is_annual=? AND COALESCE(report_type,'CFS')=?
        ORDER BY id DESC
        ''',(k['stock_code'],k['year'],k['quarter'],k['is_annual'],k['report_type'])).fetchall()
        if not rs: continue
        best=max(rs,key=qscore_cf)
        p=dict(best)
        p['report_type']=k['report_type']
        ok,fixed,_=gate_cashflow_row(conn,p)
        if not ok: continue
        upsert_canonical_cashflow(conn,fixed,source_row_id=best['id'],decision_reason='rebuild_2022_2025',quality_score=qscore_cf(best))
        cf_rows+=1

    conn.commit()
    print({'canonical_financial_rows':fin_rows,'canonical_cashflow_rows':cf_rows,'years':[Y1,Y2]})

if __name__=='__main__':
    main()
