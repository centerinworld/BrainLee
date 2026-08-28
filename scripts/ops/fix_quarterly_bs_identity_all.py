#!/usr/bin/env python3
import sqlite3
from datetime import datetime

DB='/Applications/stock_dashboard/stock.db'
RUN_ID='codex_bs_identity_all_20260530'

def main():
    conn=sqlite3.connect(DB)
    conn.row_factory=sqlite3.Row
    now=datetime.now().isoformat(timespec='seconds')

    conn.execute('''
    CREATE TABLE IF NOT EXISTS financial_fix_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      fixed_at TEXT,
      row_id INTEGER,
      stock_code TEXT,
      year INTEGER,
      quarter INTEGER,
      is_annual INTEGER,
      report_type TEXT,
      field_name TEXT,
      old_value REAL,
      new_value REAL,
      fix_rule TEXT,
      source TEXT,
      run_id TEXT
    )
    ''')

    rows=conn.execute('''
    SELECT id,stock_code,year,quarter,is_annual,report_type,data_source,
           total_assets,total_liabilities,total_equity
    FROM financial_data
    WHERE is_annual=0
      AND quarter BETWEEN 1 AND 4
      AND total_assets IS NOT NULL
      AND total_liabilities IS NOT NULL
    ''').fetchall()

    fixed=0
    by_rt={'CFS':0,'OFS':0,'NULL':0}
    for r in rows:
        ta=float(r['total_assets'])
        tl=float(r['total_liabilities'])
        te=r['total_equity']
        calc=ta-tl
        if te is None:
            old=None
            new=calc
            reason='BS_EQUITY_NULL_FILL'
        else:
            tef=float(te)
            diff=calc-tef
            tol=max(abs(ta)*0.01,5e8)
            if abs(diff)<=tol:
                continue
            old=tef
            new=calc
            reason='BS_IDENTITY_FIX'

        conn.execute('''
        INSERT INTO financial_fix_log
        (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (now,r['id'],r['stock_code'],r['year'],r['quarter'],r['is_annual'],r['report_type'] or 'CFS','total_equity',old,new,reason,'scripts/ops/fix_quarterly_bs_identity_all.py',RUN_ID))
        conn.execute("UPDATE financial_data SET total_equity=?, data_source=CASE WHEN data_source IS NULL THEN 'bs_identity_fix' ELSE data_source||'_bsfix' END WHERE id=?", (new,r['id']))
        fixed+=1
        rt=(r['report_type'] or 'NULL')
        by_rt[rt if rt in by_rt else 'NULL'] +=1

    conn.commit()

    # summarize remaining mismatches
    rem=conn.execute('''
    SELECT report_type, COUNT(*) c
    FROM financial_data
    WHERE is_annual=0 AND quarter BETWEEN 1 AND 4
      AND total_assets IS NOT NULL AND total_liabilities IS NOT NULL AND total_equity IS NOT NULL
      AND ABS((total_assets-total_liabilities)-total_equity) > MAX(ABS(total_assets)*0.01, 500000000.0)
    GROUP BY report_type
    ''').fetchall()

    print({'fixed':fixed,'by_report_type':by_rt,'remaining':[(r[0],r[1]) for r in rem]})

if __name__=='__main__':
    main()
