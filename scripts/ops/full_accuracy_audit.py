#!/usr/bin/env python3
from __future__ import annotations
import csv, json, sqlite3, time
from pathlib import Path

DB=Path('/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db')
OUT=Path('/Volumes/Realtek_NVME/stock_dashboard/runtime/scratch/full_accuracy_audit')
OUT.mkdir(parents=True, exist_ok=True)
YEARS=(2023,2024,2025)

def conn():
    c=sqlite3.connect(DB)
    c.row_factory=sqlite3.Row
    return c

def write_csv(path, rows):
    if not rows:
        path.write_text('',encoding='utf-8')
        return
    with path.open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

def q(c,sql):
    return [dict(r) for r in c.execute(sql).fetchall()]

def main():
    c=conn()
    ts=time.strftime('%Y%m%d_%H%M%S')

    # universe
    top = q(c, """
    WITH latest AS (
      SELECT stock_code, MAX(base_date) md
      FROM stock_universe
      WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
      GROUP BY stock_code
    ), u AS (
      SELECT su.stock_code, su.stock_name, su.market, COALESCE(su.market_cap,0) market_cap,
             su.per, su.pbr, su.eps
      FROM stock_universe su
      JOIN latest l ON su.stock_code=l.stock_code AND su.base_date=l.md
      WHERE su.market IN ('유가증권','코스피','코스닥','KOSPI','KOSDAQ')
        AND COALESCE(su.stock_type,'보통주')='보통주'
        AND COALESCE(su.stock_name,'') NOT LIKE '%ETF%'
        AND COALESCE(su.stock_name,'') NOT LIKE '%ETN%'
    )
    SELECT * FROM u ORDER BY market_cap DESC LIMIT 2500
    """)
    c.execute('DROP TABLE IF EXISTS temp_top2500_all')
    c.execute('CREATE TEMP TABLE temp_top2500_all(stock_code TEXT PRIMARY KEY, stock_name TEXT, market TEXT, market_cap REAL, per REAL, pbr REAL, eps REAL)')
    c.executemany('INSERT INTO temp_top2500_all VALUES (?,?,?,?,?,?,?)',[(r['stock_code'],r['stock_name'],r['market'],r['market_cap'],r['per'],r['pbr'],r['eps']) for r in top])

    # A) reference integrity/orphan checks
    orphan_price = q(c, """
    SELECT ph.stock_code, COUNT(*) rows
    FROM price_history ph
    LEFT JOIN stock_universe su ON su.stock_code=ph.stock_code
    WHERE ph.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
      AND su.stock_code IS NULL
    GROUP BY ph.stock_code
    ORDER BY rows DESC
    """)

    orphan_fin = q(c, """
    SELECT fd.stock_code, COUNT(*) rows
    FROM financial_data fd
    LEFT JOIN stock_universe su ON su.stock_code=fd.stock_code
    WHERE fd.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
      AND su.stock_code IS NULL
    GROUP BY fd.stock_code
    ORDER BY rows DESC
    """)

    # B) price integrity checks
    price_dup = q(c, """
    SELECT stock_code, date, COUNT(*) dup_cnt
    FROM price_history
    WHERE stock_code IN (SELECT stock_code FROM temp_top2500_all)
    GROUP BY stock_code, date
    HAVING COUNT(*)>1
    ORDER BY dup_cnt DESC, stock_code, date
    """)

    price_invalid = q(c, """
    SELECT stock_code, date, open, high, low, close, volume
    FROM price_history
    WHERE stock_code IN (SELECT stock_code FROM temp_top2500_all)
      AND (
        close<=0 OR open<=0 OR high<=0 OR low<=0
        OR high < low
        OR high < close OR high < open
        OR low > close OR low > open
        OR volume < 0
      )
    ORDER BY date DESC
    LIMIT 5000
    """)

    price_outlier = q(c, """
    WITH d AS (
      SELECT stock_code, date, close,
             LAG(close) OVER (PARTITION BY stock_code ORDER BY date) prev_close
      FROM price_history
      WHERE stock_code IN (SELECT stock_code FROM temp_top2500_all)
    )
    SELECT stock_code, date, close, prev_close,
           ROUND((close-prev_close)/prev_close*100,2) pct_change
    FROM d
    WHERE prev_close>0 AND ABS((close-prev_close)/prev_close) >= 0.45
    ORDER BY ABS((close-prev_close)/prev_close) DESC
    LIMIT 5000
    """)

    # C) valuation consistency internal
    valuation_internal = q(c, """
    WITH p AS (
      SELECT stock_code, close,
             ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY date DESC) rn
      FROM price_history
      WHERE stock_code IN (SELECT stock_code FROM temp_top2500_all) AND close>0
    ), f AS (
      SELECT stock_code, year, eps, bps,
             ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY year DESC, CASE WHEN data_source='fnguide' THEN 0 ELSE 1 END, id DESC) rn
      FROM financial_data
      WHERE is_annual=1 AND stock_code IN (SELECT stock_code FROM temp_top2500_all)
    )
    SELECT t.stock_code, t.stock_name, t.market,
           ROUND(t.per,2) db_per, ROUND(t.pbr,2) db_pbr, ROUND(t.eps,2) db_eps_univ,
           ROUND(p.close,2) latest_close,
           ROUND(f.eps,2) fin_eps, ROUND(f.bps,2) fin_bps,
           ROUND(CASE WHEN f.eps>0 THEN p.close/f.eps END,2) calc_per,
           ROUND(CASE WHEN f.bps>0 THEN p.close/f.bps END,2) calc_pbr,
           CASE WHEN t.per IS NOT NULL AND f.eps>0 THEN ROUND(ABS(t.per-(p.close/f.eps))/ABS(p.close/f.eps)*100,2) END per_diff_pct,
           CASE WHEN t.pbr IS NOT NULL AND f.bps>0 THEN ROUND(ABS(t.pbr-(p.close/f.bps))/ABS(p.close/f.bps)*100,2) END pbr_diff_pct
    FROM temp_top2500_all t
    LEFT JOIN p ON p.stock_code=t.stock_code AND p.rn=1
    LEFT JOIN f ON f.stock_code=t.stock_code AND f.rn=1
    WHERE p.close IS NOT NULL
      AND (
        (t.per IS NOT NULL AND f.eps>0 AND ABS(t.per-(p.close/f.eps))/ABS(p.close/f.eps) > 0.25)
        OR
        (t.pbr IS NOT NULL AND f.bps>0 AND ABS(t.pbr-(p.close/f.bps))/ABS(p.close/f.bps) > 0.25)
      )
    ORDER BY COALESCE(per_diff_pct,0) DESC, COALESCE(pbr_diff_pct,0) DESC
    """)

    # D) investor flow coverage last 3y
    investor_gap = q(c, """
    WITH recent AS (
      SELECT stock_code,
             SUM(CASE WHEN date >= date('now','-3 years') AND (inst_net_buy IS NOT NULL OR frn_net_buy IS NOT NULL) THEN 1 ELSE 0 END) has_flow_rows,
             SUM(CASE WHEN date >= date('now','-3 years') THEN 1 ELSE 0 END) all_rows
      FROM price_history
      WHERE stock_code IN (SELECT stock_code FROM temp_top2500_all)
      GROUP BY stock_code
    )
    SELECT t.stock_code, t.stock_name, t.market, r.all_rows, r.has_flow_rows
    FROM temp_top2500_all t
    JOIN recent r ON r.stock_code=t.stock_code
    WHERE COALESCE(r.all_rows,0)>0 AND COALESCE(r.has_flow_rows,0)=0
    ORDER BY r.all_rows DESC
    """)

    # E) financial/cf anomaly reuse from prior logic
    fin_anom = q(c, """
    WITH q AS (
      SELECT fd.stock_code, fd.year, fd.report_type,
             MAX(CASE WHEN fd.quarter=2 THEN fd.revenue END) q2_rev,
             MAX(CASE WHEN fd.quarter=3 THEN fd.revenue END) q3_rev,
             MAX(CASE WHEN fd.quarter=4 THEN fd.revenue END) q4_rev,
             MAX(CASE WHEN fd.quarter=3 THEN fd.operating_profit END) q3_op,
             MAX(CASE WHEN fd.quarter=4 THEN fd.operating_profit END) q4_op,
             MAX(CASE WHEN fd.quarter=3 THEN fd.net_income END) q3_ni,
             MAX(CASE WHEN fd.quarter=4 THEN fd.net_income END) q4_ni,
             MAX(COALESCE(fd.data_source,'NULL')) q_src
      FROM financial_data fd JOIN temp_top2500_all t ON t.stock_code=fd.stock_code
      WHERE fd.is_annual=0 AND fd.year IN (2023,2024,2025)
      GROUP BY fd.stock_code, fd.year, fd.report_type
    ), z AS (
      SELECT *, CASE WHEN q2_rev>0 THEN q3_rev*1.0/q2_rev END r32,
                CASE WHEN q3_rev<>0 THEN q4_rev*1.0/ABS(q3_rev) END r43
      FROM q
    )
    SELECT z.stock_code, t.stock_name, t.market, z.year, z.report_type, z.q_src,
           ROUND(z.q2_rev/1e8,1) q2_rev_uk, ROUND(z.q3_rev/1e8,1) q3_rev_uk, ROUND(z.q4_rev/1e8,1) q4_rev_uk,
           ROUND(z.q3_op/1e8,1) q3_op_uk, ROUND(z.q4_op/1e8,1) q4_op_uk,
           ROUND(z.q3_ni/1e8,1) q3_ni_uk, ROUND(z.q4_ni/1e8,1) q4_ni_uk,
           ROUND(z.r32,3) r32, ROUND(z.r43,2) r43
    FROM z JOIN temp_top2500_all t ON t.stock_code=z.stock_code
    WHERE ((z.r32 IS NOT NULL AND z.r32 < 0.20 AND z.r43 IS NOT NULL AND z.r43 > 8.0)
       OR (z.q3_op IS NOT NULL AND ABS(z.q3_op) < 5000000000 AND z.q4_op IS NOT NULL AND ABS(z.q4_op) > 80000000000)
       OR (z.q3_ni IS NOT NULL AND ABS(z.q3_ni) < 5000000000 AND z.q4_ni IS NOT NULL AND ABS(z.q4_ni) > 120000000000))
    ORDER BY z.year DESC, z.r43 DESC
    """)

    cf_anom = q(c, """
    WITH q AS (
      SELECT cf.stock_code, cf.year, cf.report_type,
             MAX(CASE WHEN cf.quarter=2 THEN COALESCE(cf.operating_cf_q,cf.operating_cf) END) q2_op,
             MAX(CASE WHEN cf.quarter=3 THEN COALESCE(cf.operating_cf_q,cf.operating_cf) END) q3_op,
             MAX(CASE WHEN cf.quarter=4 THEN COALESCE(cf.operating_cf_q,cf.operating_cf) END) q4_op,
             MAX(CASE WHEN cf.quarter=2 THEN COALESCE(cf.investing_cf_q,cf.investing_cf) END) q2_inv,
             MAX(CASE WHEN cf.quarter=3 THEN COALESCE(cf.investing_cf_q,cf.investing_cf) END) q3_inv,
             MAX(CASE WHEN cf.quarter=4 THEN COALESCE(cf.investing_cf_q,cf.investing_cf) END) q4_inv,
             MAX(CASE WHEN cf.quarter=2 THEN COALESCE(cf.financing_cf_q,cf.financing_cf) END) q2_fin,
             MAX(CASE WHEN cf.quarter=3 THEN COALESCE(cf.financing_cf_q,cf.financing_cf) END) q3_fin,
             MAX(CASE WHEN cf.quarter=4 THEN COALESCE(cf.financing_cf_q,cf.financing_cf) END) q4_fin,
             MAX(COALESCE(cf.data_source,'NULL')) q_src
      FROM cash_flow_data cf JOIN temp_top2500_all t ON t.stock_code=cf.stock_code
      WHERE cf.is_annual=0 AND cf.year IN (2023,2024,2025)
      GROUP BY cf.stock_code, cf.year, cf.report_type
    ), z AS (
      SELECT *,
        CASE WHEN q2_op IS NOT NULL AND ABS(q2_op)>0 THEN q3_op*1.0/ABS(q2_op) END r_op32,
        CASE WHEN q3_op IS NOT NULL AND ABS(q3_op)>0 THEN q4_op*1.0/ABS(q3_op) END r_op43
      FROM q
    )
    SELECT z.stock_code, t.stock_name, t.market, z.year, z.report_type, z.q_src,
           ROUND(z.q3_op/1e8,1) q3_op_uk, ROUND(z.q4_op/1e8,1) q4_op_uk,
           ROUND(z.q3_inv/1e8,1) q3_inv_uk, ROUND(z.q4_inv/1e8,1) q4_inv_uk,
           ROUND(z.q3_fin/1e8,1) q3_fin_uk, ROUND(z.q4_fin/1e8,1) q4_fin_uk,
           ROUND(z.r_op32,2) r_op32, ROUND(z.r_op43,2) r_op43
    FROM z JOIN temp_top2500_all t ON t.stock_code=z.stock_code
    WHERE ((z.q3_op IS NOT NULL AND ABS(z.q3_op) < 10000000000 AND z.q4_op IS NOT NULL AND ABS(z.q4_op) > 200000000000)
       OR (z.r_op32 IS NOT NULL AND ABS(z.r_op32) < 0.2 AND z.r_op43 IS NOT NULL AND ABS(z.r_op43) > 8))
    ORDER BY z.year DESC
    """)

    # F) fnguide snapshot coverage gaps
    fin_gap = q(c, """
    SELECT fd.stock_code, t.stock_name, t.market, fd.year, fd.quarter, fd.report_type, COALESCE(fd.data_source,'NULL') fd_source
    FROM financial_data fd
    JOIN temp_top2500_all t ON t.stock_code=fd.stock_code
    LEFT JOIN financial_source_snapshot fss
      ON fss.stock_code=fd.stock_code AND fss.year=fd.year AND fss.quarter=fd.quarter
     AND fss.is_annual=fd.is_annual AND fss.report_type=fd.report_type AND fss.data_source='fnguide'
    WHERE fd.is_annual=0 AND fd.year IN (2023,2024,2025) AND fss.stock_code IS NULL
      AND fd.data_source='fnguide'
    ORDER BY fd.year DESC, fd.stock_code, fd.quarter
    """)

    cf_gap = q(c, """
    SELECT cf.stock_code, t.stock_name, t.market, cf.year, cf.quarter, cf.report_type, COALESCE(cf.data_source,'NULL') cf_source
    FROM cash_flow_data cf
    JOIN temp_top2500_all t ON t.stock_code=cf.stock_code
    LEFT JOIN financial_source_snapshot fss
      ON fss.stock_code=cf.stock_code AND fss.year=cf.year AND fss.quarter=cf.quarter
     AND fss.is_annual=cf.is_annual AND fss.report_type=cf.report_type AND fss.data_source='fnguide'
    WHERE cf.is_annual=0 AND cf.year IN (2023,2024,2025) AND fss.stock_code IS NULL
      AND cf.data_source='fnguide'
    ORDER BY cf.year DESC, cf.stock_code, cf.quarter
    """)

    files={
      'orphan_price': OUT/f'orphan_price_{ts}.csv',
      'orphan_financial': OUT/f'orphan_financial_{ts}.csv',
      'price_duplicates': OUT/f'price_duplicates_{ts}.csv',
      'price_invalid_ohlcv': OUT/f'price_invalid_ohlcv_{ts}.csv',
      'price_extreme_changes': OUT/f'price_extreme_changes_{ts}.csv',
      'valuation_internal_mismatch': OUT/f'valuation_internal_mismatch_{ts}.csv',
      'investor_flow_missing_3y': OUT/f'investor_flow_missing_3y_{ts}.csv',
      'financial_quarter_anomaly': OUT/f'financial_quarter_anomaly_{ts}.csv',
      'cashflow_quarter_anomaly': OUT/f'cashflow_quarter_anomaly_{ts}.csv',
      'financial_snapshot_gap': OUT/f'financial_snapshot_gap_{ts}.csv',
      'cashflow_snapshot_gap': OUT/f'cashflow_snapshot_gap_{ts}.csv',
    }

    data_map={
      'orphan_price':orphan_price,
      'orphan_financial':orphan_fin,
      'price_duplicates':price_dup,
      'price_invalid_ohlcv':price_invalid,
      'price_extreme_changes':price_outlier,
      'valuation_internal_mismatch':valuation_internal,
      'investor_flow_missing_3y':investor_gap,
      'financial_quarter_anomaly':fin_anom,
      'cashflow_quarter_anomaly':cf_anom,
      'financial_snapshot_gap':fin_gap,
      'cashflow_snapshot_gap':cf_gap,
    }

    for k,p in files.items():
      write_csv(p, data_map[k])

    # priority lists
    p1_fin=[r for r in fin_anom if r.get('q_src')=='NULL' and str(r.get('year')) in ('2023','2024')]
    p1_cf=[r for r in cf_anom if r.get('q_src')=='NULL' and str(r.get('year')) in ('2023','2024')]

    summary={
      'scope': {'top_n':2500,'years':list(YEARS),'generated_at':ts},
      'counts': {k: len(v) for k,v in data_map.items()},
      'priority': {
        'financial_nullsource_q_anomaly_rows': len(p1_fin),
        'cashflow_nullsource_q_anomaly_rows': len(p1_cf),
        'financial_nullsource_q_anomaly_unique_stocks': len({r['stock_code'] for r in p1_fin}),
        'cashflow_nullsource_q_anomaly_unique_stocks': len({r['stock_code'] for r in p1_cf}),
      },
      'core_root_causes': [
        '2023~2024 분기 데이터에 legacy NULL-source 잔존',
        'financial_source_snapshot 분기 키 커버리지 공백으로 검증/덮어쓰기 누락',
        'Q4 재계산 및 누적-증분 변환 이력에 따른 일부 분기 왜곡'
      ],
      'output_files': {k:str(v) for k,v in files.items()},
      'previous_external_baseline_600': {
        'revenue': '579건 비교, 100.0% 일치',
        'operating_profit': '582건 비교, 99.83% 일치',
        'net_income': '582건 비교, 96.91% 일치',
        'EPS_financialData_vs_FnGuideMain': '432건 비교, 8.10% 일치',
        'BPS_financialData_vs_FnGuideMain': '450건 비교, 33.56% 일치',
        'PER_stockUniverse_vs_Naver': '434건 비교, 65.67% 일치',
        'PBR_stockUniverse_vs_Naver': '591건 비교, 68.19% 일치',
        'EPS_stockUniverse_vs_Naver': '591건 비교, 99.83% 일치'
      }
    }

    j=OUT/f'full_accuracy_audit_summary_{ts}.json'
    j.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
