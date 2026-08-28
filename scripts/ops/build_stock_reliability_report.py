#!/usr/bin/env python3
import sqlite3, csv
from datetime import datetime

DB='/Applications/stock_dashboard/stock.db'
OUT='/Applications/stock_dashboard/scratch/stock_reliability_report_{}.csv'.format(datetime.now().strftime('%Y%m%d_%H%M%S'))

conn=sqlite3.connect(DB)
conn.row_factory=sqlite3.Row

sql='''
WITH fin AS (
  SELECT stock_code,
         SUM(CASE WHEN is_annual=0 AND quarter BETWEEN 1 AND 4 THEN 1 ELSE 0 END) q_rows,
         SUM(CASE WHEN is_annual=0 AND quarter BETWEEN 1 AND 4 AND revenue IS NOT NULL THEN 1 ELSE 0 END) q_rev,
         SUM(CASE WHEN is_annual=0 AND quarter BETWEEN 1 AND 4 AND operating_profit IS NOT NULL THEN 1 ELSE 0 END) q_op,
         SUM(CASE WHEN is_annual=0 AND quarter BETWEEN 1 AND 4 AND net_income IS NOT NULL THEN 1 ELSE 0 END) q_ni,
         SUM(CASE WHEN is_annual=0 AND quarter BETWEEN 1 AND 4 AND total_assets IS NOT NULL THEN 1 ELSE 0 END) q_assets,
         SUM(CASE WHEN is_annual=0 AND quarter BETWEEN 1 AND 4 AND total_liabilities IS NOT NULL THEN 1 ELSE 0 END) q_liab,
         SUM(CASE WHEN is_annual=0 AND quarter BETWEEN 1 AND 4 AND total_equity IS NOT NULL THEN 1 ELSE 0 END) q_equity,
         SUM(CASE WHEN is_annual=0 AND quarter BETWEEN 1 AND 4 AND total_assets IS NOT NULL AND total_liabilities IS NOT NULL AND total_equity IS NOT NULL
                  AND ABS((total_assets-total_liabilities)-total_equity) <= MAX(ABS(total_assets)*0.01,500000000.0)
                  THEN 1 ELSE 0 END) q_bs_ok
  FROM financial_data
  WHERE year BETWEEN 2022 AND 2025
  GROUP BY stock_code
), cf AS (
  SELECT stock_code,
         SUM(CASE WHEN is_annual=0 AND quarter BETWEEN 1 AND 4 THEN 1 ELSE 0 END) cf_rows,
         SUM(CASE WHEN is_annual=0 AND quarter BETWEEN 1 AND 4 AND operating_cf_q IS NOT NULL THEN 1 ELSE 0 END) cf_ocf_q,
         SUM(CASE WHEN is_annual=0 AND quarter BETWEEN 1 AND 4 AND investing_cf_q IS NOT NULL THEN 1 ELSE 0 END) cf_icf_q,
         SUM(CASE WHEN is_annual=0 AND quarter BETWEEN 1 AND 4 AND financing_cf_q IS NOT NULL THEN 1 ELSE 0 END) cf_fcf_q,
         SUM(CASE WHEN is_annual=0 AND quarter BETWEEN 1 AND 4 AND capex_q IS NOT NULL THEN 1 ELSE 0 END) cf_capex_q
  FROM cash_flow_data
  WHERE year BETWEEN 2022 AND 2025
  GROUP BY stock_code
), v AS (
  SELECT stock_code,
         COUNT(*) val_cnt,
         SUM(CASE WHEN status IN ('CONFIRMED','CLOSE_MATCH','SELF_CONSISTENT') THEN 1 ELSE 0 END) val_ok
  FROM fin_quarterly_validation_flags
  WHERE year BETWEEN 2022 AND 2025
  GROUP BY stock_code
)
SELECT su.stock_code, su.stock_name,
       COALESCE(fin.q_rows,0) q_rows,
       COALESCE(fin.q_rev,0) q_rev,
       COALESCE(fin.q_op,0) q_op,
       COALESCE(fin.q_ni,0) q_ni,
       COALESCE(fin.q_assets,0) q_assets,
       COALESCE(fin.q_liab,0) q_liab,
       COALESCE(fin.q_equity,0) q_equity,
       COALESCE(fin.q_bs_ok,0) q_bs_ok,
       COALESCE(cf.cf_rows,0) cf_rows,
       COALESCE(cf.cf_ocf_q,0) cf_ocf_q,
       COALESCE(cf.cf_icf_q,0) cf_icf_q,
       COALESCE(cf.cf_fcf_q,0) cf_fcf_q,
       COALESCE(cf.cf_capex_q,0) cf_capex_q,
       COALESCE(v.val_cnt,0) val_cnt,
       COALESCE(v.val_ok,0) val_ok
FROM stock_universe su
LEFT JOIN fin ON fin.stock_code=su.stock_code
LEFT JOIN cf ON cf.stock_code=su.stock_code
LEFT JOIN v ON v.stock_code=su.stock_code
WHERE su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
ORDER BY su.stock_code
'''

rows=conn.execute(sql).fetchall()

with open(OUT,'w',newline='',encoding='utf-8-sig') as f:
    w=csv.writer(f)
    w.writerow(['stock_code','stock_name','q_rows','q_rev','q_op','q_ni','q_assets','q_liab','q_equity','q_bs_ok','cf_rows','cf_ocf_q','cf_icf_q','cf_fcf_q','cf_capex_q','val_cnt','val_ok','reliability_score'])
    for r in rows:
        q_rows=r['q_rows'] or 0
        cf_rows=r['cf_rows'] or 0
        val_cnt=r['val_cnt'] or 0
        fin_cov=((r['q_rev']+r['q_op']+r['q_ni'])/(max(q_rows,1)*3))*40 if q_rows else 0
        bs_cov=(r['q_bs_ok']/max(q_rows,1))*30 if q_rows else 0
        cf_cov=((r['cf_ocf_q']+r['cf_icf_q']+r['cf_fcf_q'])/(max(cf_rows,1)*3))*20 if cf_rows else 0
        val_cov=(r['val_ok']/max(val_cnt,1))*10 if val_cnt else 0
        score=round(fin_cov+bs_cov+cf_cov+val_cov,2)
        w.writerow([r['stock_code'],r['stock_name'],r['q_rows'],r['q_rev'],r['q_op'],r['q_ni'],r['q_assets'],r['q_liab'],r['q_equity'],r['q_bs_ok'],r['cf_rows'],r['cf_ocf_q'],r['cf_icf_q'],r['cf_fcf_q'],r['cf_capex_q'],r['val_cnt'],r['val_ok'],score])

print(OUT)
