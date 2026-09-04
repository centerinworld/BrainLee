#!/usr/bin/env python3
from __future__ import annotations
import csv, json, sqlite3, time
from pathlib import Path

DB=Path('/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db')
OUT=Path('/Volumes/Realtek_NVME/stock_dashboard/runtime/scratch/audit_top2500')
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
        w=csv.DictWriter(f,fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)


def top2500_codes(c):
    q="""
    WITH latest AS (
      SELECT stock_code, MAX(base_date) md
      FROM stock_universe
      WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
      GROUP BY stock_code
    ), u AS (
      SELECT su.stock_code, su.stock_name, su.market, COALESCE(su.market_cap,0) market_cap
      FROM stock_universe su
      JOIN latest l ON su.stock_code=l.stock_code AND su.base_date=l.md
      WHERE su.market IN ('유가증권','코스피','코스닥','KOSPI','KOSDAQ')
        AND COALESCE(su.stock_type,'보통주')='보통주'
        AND COALESCE(su.stock_name,'') NOT LIKE '%ETF%'
        AND COALESCE(su.stock_name,'') NOT LIKE '%ETN%'
    )
    SELECT stock_code, stock_name, market, market_cap
    FROM u
    ORDER BY market_cap DESC
    LIMIT 2500
    """
    return c.execute(q).fetchall()


def build_temp_top(c, top):
    c.execute('DROP TABLE IF EXISTS temp_top2500')
    c.execute('CREATE TEMP TABLE temp_top2500(stock_code TEXT PRIMARY KEY, stock_name TEXT, market TEXT, market_cap REAL)')
    c.executemany('INSERT INTO temp_top2500(stock_code,stock_name,market,market_cap) VALUES (?,?,?,?)',
                  [(r['stock_code'],r['stock_name'],r['market'],r['market_cap']) for r in top])


def run():
    c=conn()
    top=top2500_codes(c)
    build_temp_top(c, top)

    # 1) 손익 분기 이상패턴 (KAI형)
    fin_anom_q = c.execute("""
    WITH q AS (
      SELECT fd.stock_code, fd.year, fd.report_type,
             MAX(CASE WHEN fd.quarter=1 THEN fd.revenue END) q1_rev,
             MAX(CASE WHEN fd.quarter=2 THEN fd.revenue END) q2_rev,
             MAX(CASE WHEN fd.quarter=3 THEN fd.revenue END) q3_rev,
             MAX(CASE WHEN fd.quarter=4 THEN fd.revenue END) q4_rev,
             MAX(CASE WHEN fd.quarter=3 THEN fd.operating_profit END) q3_op,
             MAX(CASE WHEN fd.quarter=4 THEN fd.operating_profit END) q4_op,
             MAX(CASE WHEN fd.quarter=3 THEN fd.net_income END) q3_ni,
             MAX(CASE WHEN fd.quarter=4 THEN fd.net_income END) q4_ni,
             MAX(COALESCE(fd.data_source,'NULL')) q_src
      FROM financial_data fd JOIN temp_top2500 t ON t.stock_code=fd.stock_code
      WHERE fd.is_annual=0 AND fd.year IN (2023,2024,2025)
      GROUP BY fd.stock_code, fd.year, fd.report_type
    ), z AS (
      SELECT q.*, CASE WHEN q2_rev>0 THEN q3_rev*1.0/q2_rev END r32,
                   CASE WHEN q3_rev<>0 THEN q4_rev*1.0/ABS(q3_rev) END r43
      FROM q
    )
    SELECT z.stock_code, t.stock_name, t.market, z.year, z.report_type, z.q_src,
           ROUND(z.q1_rev/1e8,1) q1_rev_uk, ROUND(z.q2_rev/1e8,1) q2_rev_uk,
           ROUND(z.q3_rev/1e8,1) q3_rev_uk, ROUND(z.q4_rev/1e8,1) q4_rev_uk,
           ROUND(z.q3_op/1e8,1) q3_op_uk, ROUND(z.q4_op/1e8,1) q4_op_uk,
           ROUND(z.q3_ni/1e8,1) q3_ni_uk, ROUND(z.q4_ni/1e8,1) q4_ni_uk,
           ROUND(z.r32,3) r32, ROUND(z.r43,2) r43,
           CASE
             WHEN (z.r32 IS NOT NULL AND z.r32 < 0.20 AND z.r43 IS NOT NULL AND z.r43 > 8.0) THEN 'REV_COLLAPSE_Q3_Q4_SPIKE'
             WHEN (z.q3_op IS NOT NULL AND ABS(z.q3_op) < 5000000000 AND z.q4_op IS NOT NULL AND ABS(z.q4_op) > 80000000000) THEN 'OP_Q4_SPIKE'
             WHEN (z.q3_ni IS NOT NULL AND ABS(z.q3_ni) < 5000000000 AND z.q4_ni IS NOT NULL AND ABS(z.q4_ni) > 120000000000) THEN 'NI_Q4_SPIKE'
             ELSE 'OTHER'
           END anomaly_type
    FROM z JOIN temp_top2500 t ON t.stock_code=z.stock_code
    WHERE ((z.r32 IS NOT NULL AND z.r32 < 0.20 AND z.r43 IS NOT NULL AND z.r43 > 8.0)
       OR (z.q3_op IS NOT NULL AND ABS(z.q3_op) < 5000000000 AND z.q4_op IS NOT NULL AND ABS(z.q4_op) > 80000000000)
       OR (z.q3_ni IS NOT NULL AND ABS(z.q3_ni) < 5000000000 AND z.q4_ni IS NOT NULL AND ABS(z.q4_ni) > 120000000000))
    ORDER BY z.year DESC, z.r43 DESC
    """).fetchall()

    # 2) 손익 연간 vs 분기합 불일치
    fin_mismatch = c.execute("""
    WITH a AS (
      SELECT fd.stock_code, fd.year, fd.report_type, fd.data_source a_src,
             fd.revenue a_rev, fd.operating_profit a_op, fd.net_income a_ni
      FROM financial_data fd JOIN temp_top2500 t ON t.stock_code=fd.stock_code
      WHERE fd.is_annual=1 AND fd.year IN (2023,2024,2025)
    ), q AS (
      SELECT fd.stock_code, fd.year, fd.report_type,
             SUM(COALESCE(fd.revenue,0)) s_rev,
             SUM(COALESCE(fd.operating_profit,0)) s_op,
             SUM(COALESCE(fd.net_income,0)) s_ni,
             MAX(COALESCE(fd.data_source,'NULL')) q_src
      FROM financial_data fd JOIN temp_top2500 t ON t.stock_code=fd.stock_code
      WHERE fd.is_annual=0 AND fd.year IN (2023,2024,2025)
      GROUP BY fd.stock_code, fd.year, fd.report_type
    )
    SELECT a.stock_code, t.stock_name, t.market, a.year, a.report_type,
           COALESCE(a.a_src,'NULL') a_src, q.q_src,
           ROUND(a.a_rev/1e8,1) a_rev_uk, ROUND(q.s_rev/1e8,1) s_rev_uk,
           ROUND((q.s_rev-a.a_rev)/1e8,1) diff_rev_uk,
           ROUND(a.a_op/1e8,1) a_op_uk, ROUND(q.s_op/1e8,1) s_op_uk,
           ROUND((q.s_op-a.a_op)/1e8,1) diff_op_uk,
           ROUND(a.a_ni/1e8,1) a_ni_uk, ROUND(q.s_ni/1e8,1) s_ni_uk,
           ROUND((q.s_ni-a.a_ni)/1e8,1) diff_ni_uk,
           CASE WHEN ABS(a.a_rev)>0 THEN ROUND(ABS(q.s_rev-a.a_rev)/ABS(a.a_rev)*100,2) END diff_rev_pct,
           CASE WHEN ABS(a.a_op)>0 THEN ROUND(ABS(q.s_op-a.a_op)/ABS(a.a_op)*100,2) END diff_op_pct,
           CASE WHEN ABS(a.a_ni)>0 THEN ROUND(ABS(q.s_ni-a.a_ni)/ABS(a.a_ni)*100,2) END diff_ni_pct
    FROM a JOIN q ON q.stock_code=a.stock_code AND q.year=a.year AND q.report_type=a.report_type
    JOIN temp_top2500 t ON t.stock_code=a.stock_code
    WHERE (ABS(q.s_rev-a.a_rev) > 100000000000 OR ABS(q.s_op-a.a_op) > 80000000000 OR ABS(q.s_ni-a.a_ni) > 120000000000)
    ORDER BY a.year DESC, ABS(q.s_rev-a.a_rev) DESC
    """).fetchall()

    # 3) 손익 분기의 FnGuide snapshot coverage 결손
    fin_snapshot_gap = c.execute("""
    WITH fd AS (
      SELECT fd.stock_code, fd.year, fd.quarter, fd.is_annual, fd.report_type,
             COALESCE(fd.data_source,'NULL') fd_source,
             fd.revenue, fd.operating_profit, fd.net_income
      FROM financial_data fd JOIN temp_top2500 t ON t.stock_code=fd.stock_code
      WHERE fd.is_annual=0 AND fd.year IN (2023,2024,2025)
    )
    SELECT fd.stock_code, t.stock_name, t.market, fd.year, fd.quarter, fd.report_type,
           fd.fd_source,
           CASE WHEN fss.stock_code IS NULL THEN 0 ELSE 1 END has_fnguide_snapshot_same_key,
           ROUND(fd.revenue/1e8,1) rev_uk, ROUND(fd.operating_profit/1e8,1) op_uk, ROUND(fd.net_income/1e8,1) ni_uk
    FROM fd JOIN temp_top2500 t ON t.stock_code=fd.stock_code
    LEFT JOIN financial_source_snapshot fss
      ON fss.stock_code=fd.stock_code AND fss.year=fd.year AND fss.quarter=fd.quarter
     AND fss.is_annual=fd.is_annual AND fss.report_type=fd.report_type AND fss.data_source='fnguide'
    WHERE fss.stock_code IS NULL
    ORDER BY fd.year DESC, fd.stock_code, fd.quarter
    """).fetchall()

    # 4) 현금흐름 분기 이상패턴 (Q3 작고 Q4 급반등 등)
    cf_anom_q = c.execute("""
    WITH q AS (
      SELECT cf.stock_code, cf.year, cf.report_type,
             MAX(CASE WHEN cf.quarter=1 THEN COALESCE(cf.operating_cf_q, cf.operating_cf) END) q1_op,
             MAX(CASE WHEN cf.quarter=2 THEN COALESCE(cf.operating_cf_q, cf.operating_cf) END) q2_op,
             MAX(CASE WHEN cf.quarter=3 THEN COALESCE(cf.operating_cf_q, cf.operating_cf) END) q3_op,
             MAX(CASE WHEN cf.quarter=4 THEN COALESCE(cf.operating_cf_q, cf.operating_cf) END) q4_op,
             MAX(CASE WHEN cf.quarter=1 THEN COALESCE(cf.investing_cf_q, cf.investing_cf) END) q1_inv,
             MAX(CASE WHEN cf.quarter=2 THEN COALESCE(cf.investing_cf_q, cf.investing_cf) END) q2_inv,
             MAX(CASE WHEN cf.quarter=3 THEN COALESCE(cf.investing_cf_q, cf.investing_cf) END) q3_inv,
             MAX(CASE WHEN cf.quarter=4 THEN COALESCE(cf.investing_cf_q, cf.investing_cf) END) q4_inv,
             MAX(CASE WHEN cf.quarter=1 THEN COALESCE(cf.financing_cf_q, cf.financing_cf) END) q1_fin,
             MAX(CASE WHEN cf.quarter=2 THEN COALESCE(cf.financing_cf_q, cf.financing_cf) END) q2_fin,
             MAX(CASE WHEN cf.quarter=3 THEN COALESCE(cf.financing_cf_q, cf.financing_cf) END) q3_fin,
             MAX(CASE WHEN cf.quarter=4 THEN COALESCE(cf.financing_cf_q, cf.financing_cf) END) q4_fin,
             MAX(COALESCE(cf.data_source,'NULL')) q_src
      FROM cash_flow_data cf JOIN temp_top2500 t ON t.stock_code=cf.stock_code
      WHERE cf.is_annual=0 AND cf.year IN (2023,2024,2025)
      GROUP BY cf.stock_code, cf.year, cf.report_type
    ), z AS (
      SELECT *,
        CASE WHEN q2_op IS NOT NULL AND ABS(q2_op)>0 THEN q3_op*1.0/ABS(q2_op) END r_op32,
        CASE WHEN q3_op IS NOT NULL AND ABS(q3_op)>0 THEN q4_op*1.0/ABS(q3_op) END r_op43,
        CASE WHEN q2_inv IS NOT NULL AND ABS(q2_inv)>0 THEN q3_inv*1.0/ABS(q2_inv) END r_inv32,
        CASE WHEN q3_inv IS NOT NULL AND ABS(q3_inv)>0 THEN q4_inv*1.0/ABS(q3_inv) END r_inv43,
        CASE WHEN q2_fin IS NOT NULL AND ABS(q2_fin)>0 THEN q3_fin*1.0/ABS(q2_fin) END r_fin32,
        CASE WHEN q3_fin IS NOT NULL AND ABS(q3_fin)>0 THEN q4_fin*1.0/ABS(q3_fin) END r_fin43
      FROM q
    )
    SELECT z.stock_code, t.stock_name, t.market, z.year, z.report_type, z.q_src,
           ROUND(z.q3_op/1e8,1) q3_op_uk, ROUND(z.q4_op/1e8,1) q4_op_uk,
           ROUND(z.q3_inv/1e8,1) q3_inv_uk, ROUND(z.q4_inv/1e8,1) q4_inv_uk,
           ROUND(z.q3_fin/1e8,1) q3_fin_uk, ROUND(z.q4_fin/1e8,1) q4_fin_uk,
           ROUND(z.r_op43,2) r_op43, ROUND(z.r_inv43,2) r_inv43, ROUND(z.r_fin43,2) r_fin43,
           CASE
             WHEN z.q3_op IS NOT NULL AND ABS(z.q3_op) < 10000000000 AND z.q4_op IS NOT NULL AND ABS(z.q4_op) > 200000000000 THEN 'OP_CF_Q4_SPIKE'
             WHEN z.q3_inv IS NOT NULL AND ABS(z.q3_inv) < 10000000000 AND z.q4_inv IS NOT NULL AND ABS(z.q4_inv) > 300000000000 THEN 'INV_CF_Q4_SPIKE'
             WHEN z.q3_fin IS NOT NULL AND ABS(z.q3_fin) < 10000000000 AND z.q4_fin IS NOT NULL AND ABS(z.q4_fin) > 300000000000 THEN 'FIN_CF_Q4_SPIKE'
             WHEN z.r_op32 IS NOT NULL AND ABS(z.r_op32) < 0.2 AND z.r_op43 IS NOT NULL AND ABS(z.r_op43) > 8 THEN 'OP_CF_COLLAPSE_SPIKE'
             ELSE 'OTHER'
           END anomaly_type
    FROM z JOIN temp_top2500 t ON t.stock_code=z.stock_code
    WHERE ((z.q3_op IS NOT NULL AND ABS(z.q3_op) < 10000000000 AND z.q4_op IS NOT NULL AND ABS(z.q4_op) > 200000000000)
       OR (z.q3_inv IS NOT NULL AND ABS(z.q3_inv) < 10000000000 AND z.q4_inv IS NOT NULL AND ABS(z.q4_inv) > 300000000000)
       OR (z.q3_fin IS NOT NULL AND ABS(z.q3_fin) < 10000000000 AND z.q4_fin IS NOT NULL AND ABS(z.q4_fin) > 300000000000)
       OR (z.r_op32 IS NOT NULL AND ABS(z.r_op32) < 0.2 AND z.r_op43 IS NOT NULL AND ABS(z.r_op43) > 8))
    ORDER BY z.year DESC
    """).fetchall()

    # 5) 현금흐름 연간 vs 분기합 불일치
    cf_mismatch = c.execute("""
    WITH a AS (
      SELECT cf.stock_code, cf.year, cf.report_type, COALESCE(cf.data_source,'NULL') a_src,
             cf.operating_cf a_op, cf.investing_cf a_inv, cf.financing_cf a_fin, cf.capex a_cap
      FROM cash_flow_data cf JOIN temp_top2500 t ON t.stock_code=cf.stock_code
      WHERE cf.is_annual=1 AND cf.year IN (2023,2024,2025)
    ), q AS (
      SELECT cf.stock_code, cf.year, cf.report_type, MAX(COALESCE(cf.data_source,'NULL')) q_src,
             SUM(COALESCE(cf.operating_cf_q, cf.operating_cf,0)) s_op,
             SUM(COALESCE(cf.investing_cf_q, cf.investing_cf,0)) s_inv,
             SUM(COALESCE(cf.financing_cf_q, cf.financing_cf,0)) s_fin,
             SUM(COALESCE(cf.capex_q, cf.capex,0)) s_cap
      FROM cash_flow_data cf JOIN temp_top2500 t ON t.stock_code=cf.stock_code
      WHERE cf.is_annual=0 AND cf.year IN (2023,2024,2025)
      GROUP BY cf.stock_code, cf.year, cf.report_type
    )
    SELECT a.stock_code, t.stock_name, t.market, a.year, a.report_type, a.a_src, q.q_src,
           ROUND(a.a_op/1e8,1) a_op_uk, ROUND(q.s_op/1e8,1) s_op_uk, ROUND((q.s_op-a.a_op)/1e8,1) diff_op_uk,
           ROUND(a.a_inv/1e8,1) a_inv_uk, ROUND(q.s_inv/1e8,1) s_inv_uk, ROUND((q.s_inv-a.a_inv)/1e8,1) diff_inv_uk,
           ROUND(a.a_fin/1e8,1) a_fin_uk, ROUND(q.s_fin/1e8,1) s_fin_uk, ROUND((q.s_fin-a.a_fin)/1e8,1) diff_fin_uk,
           ROUND(a.a_cap/1e8,1) a_cap_uk, ROUND(q.s_cap/1e8,1) s_cap_uk, ROUND((q.s_cap-a.a_cap)/1e8,1) diff_cap_uk,
           CASE WHEN ABS(a.a_op)>0 THEN ROUND(ABS(q.s_op-a.a_op)/ABS(a.a_op)*100,2) END diff_op_pct,
           CASE WHEN ABS(a.a_inv)>0 THEN ROUND(ABS(q.s_inv-a.a_inv)/ABS(a.a_inv)*100,2) END diff_inv_pct,
           CASE WHEN ABS(a.a_fin)>0 THEN ROUND(ABS(q.s_fin-a.a_fin)/ABS(a.a_fin)*100,2) END diff_fin_pct
    FROM a JOIN q ON q.stock_code=a.stock_code AND q.year=a.year AND q.report_type=a.report_type
    JOIN temp_top2500 t ON t.stock_code=a.stock_code
    WHERE (ABS(q.s_op-a.a_op) > 120000000000 OR ABS(q.s_inv-a.a_inv) > 150000000000 OR ABS(q.s_fin-a.a_fin) > 150000000000)
    ORDER BY a.year DESC, ABS(q.s_op-a.a_op) DESC
    """).fetchall()

    # 6) 현금흐름 분기 snapshot coverage 결손
    cf_snapshot_gap = c.execute("""
    WITH cf AS (
      SELECT cf.stock_code, cf.year, cf.quarter, cf.is_annual, cf.report_type,
             COALESCE(cf.data_source,'NULL') cf_source,
             COALESCE(cf.operating_cf_q,cf.operating_cf) operating_cf,
             COALESCE(cf.investing_cf_q,cf.investing_cf) investing_cf,
             COALESCE(cf.financing_cf_q,cf.financing_cf) financing_cf,
             COALESCE(cf.capex_q,cf.capex) capex
      FROM cash_flow_data cf JOIN temp_top2500 t ON t.stock_code=cf.stock_code
      WHERE cf.is_annual=0 AND cf.year IN (2023,2024,2025)
    )
    SELECT cf.stock_code, t.stock_name, t.market, cf.year, cf.quarter, cf.report_type,
           cf.cf_source,
           CASE WHEN fss.stock_code IS NULL THEN 0 ELSE 1 END has_fnguide_snapshot_same_key,
           ROUND(cf.operating_cf/1e8,1) op_cf_uk,
           ROUND(cf.investing_cf/1e8,1) inv_cf_uk,
           ROUND(cf.financing_cf/1e8,1) fin_cf_uk,
           ROUND(cf.capex/1e8,1) capex_uk
    FROM cf JOIN temp_top2500 t ON t.stock_code=cf.stock_code
    LEFT JOIN financial_source_snapshot fss
      ON fss.stock_code=cf.stock_code AND fss.year=cf.year AND fss.quarter=cf.quarter
     AND fss.is_annual=cf.is_annual AND fss.report_type=cf.report_type AND fss.data_source='fnguide'
    WHERE fss.stock_code IS NULL
    ORDER BY cf.year DESC, cf.stock_code, cf.quarter
    """).fetchall()

    # 7) EPS/BPS annual vs fnguide snapshot mismatch (DB internal compare)
    eps_bps_mismatch = c.execute("""
    WITH fa AS (
      SELECT fd.stock_code, fd.year, fd.report_type,
             fd.eps db_eps, fd.bps db_bps,
             fss.eps fg_eps, fss.bps fg_bps,
             COALESCE(fd.data_source,'NULL') fd_src
      FROM financial_data fd
      JOIN temp_top2500 t ON t.stock_code=fd.stock_code
      JOIN financial_source_snapshot fss
        ON fss.stock_code=fd.stock_code AND fss.year=fd.year AND fss.quarter=0
       AND fss.is_annual=1 AND fss.report_type=fd.report_type AND fss.data_source='fnguide'
      WHERE fd.is_annual=1 AND fd.year IN (2023,2024,2025)
    )
    SELECT fa.stock_code, t.stock_name, t.market, fa.year, fa.report_type, fa.fd_src,
           fa.db_eps, fa.fg_eps,
           CASE WHEN fa.db_eps IS NOT NULL AND fa.fg_eps IS NOT NULL AND ABS(fa.fg_eps)>0 THEN ROUND(ABS(fa.db_eps-fa.fg_eps)/ABS(fa.fg_eps)*100,2) END eps_diff_pct,
           fa.db_bps, fa.fg_bps,
           CASE WHEN fa.db_bps IS NOT NULL AND fa.fg_bps IS NOT NULL AND ABS(fa.fg_bps)>0 THEN ROUND(ABS(fa.db_bps-fa.fg_bps)/ABS(fa.fg_bps)*100,2) END bps_diff_pct
    FROM fa JOIN temp_top2500 t ON t.stock_code=fa.stock_code
    WHERE ((fa.db_eps IS NOT NULL AND fa.fg_eps IS NOT NULL AND ABS(fa.db_eps-fa.fg_eps) > 30 AND ABS(fa.db_eps-fa.fg_eps)/ABS(fa.fg_eps) > 0.05)
        OR (fa.db_bps IS NOT NULL AND fa.fg_bps IS NOT NULL AND ABS(fa.db_bps-fa.fg_bps) > 80 AND ABS(fa.db_bps-fa.fg_bps)/ABS(fa.fg_bps) > 0.05))
    ORDER BY fa.year DESC, eps_diff_pct DESC, bps_diff_pct DESC
    """).fetchall()

    ts=time.strftime('%Y%m%d_%H%M%S')
    paths={
      'fin_anomaly_quarter': OUT/f'fin_anomaly_quarter_{ts}.csv',
      'fin_annual_vs_quarter_sum_mismatch': OUT/f'fin_annual_vs_quarter_sum_mismatch_{ts}.csv',
      'fin_snapshot_gap_quarter': OUT/f'fin_snapshot_gap_quarter_{ts}.csv',
      'cf_anomaly_quarter': OUT/f'cf_anomaly_quarter_{ts}.csv',
      'cf_annual_vs_quarter_sum_mismatch': OUT/f'cf_annual_vs_quarter_sum_mismatch_{ts}.csv',
      'cf_snapshot_gap_quarter': OUT/f'cf_snapshot_gap_quarter_{ts}.csv',
      'eps_bps_mismatch_annual_vs_fnguide_snapshot': OUT/f'eps_bps_mismatch_annual_vs_fnguide_snapshot_{ts}.csv',
    }

    write_csv(paths['fin_anomaly_quarter'], [dict(r) for r in fin_anom_q])
    write_csv(paths['fin_annual_vs_quarter_sum_mismatch'], [dict(r) for r in fin_mismatch])
    write_csv(paths['fin_snapshot_gap_quarter'], [dict(r) for r in fin_snapshot_gap])
    write_csv(paths['cf_anomaly_quarter'], [dict(r) for r in cf_anom_q])
    write_csv(paths['cf_annual_vs_quarter_sum_mismatch'], [dict(r) for r in cf_mismatch])
    write_csv(paths['cf_snapshot_gap_quarter'], [dict(r) for r in cf_snapshot_gap])
    write_csv(paths['eps_bps_mismatch_annual_vs_fnguide_snapshot'], [dict(r) for r in eps_bps_mismatch])

    summary={
      'scope': {'top_n': 2500, 'years': list(YEARS), 'generated_at': ts},
      'counts': {
        'fin_anomaly_quarter': len(fin_anom_q),
        'fin_annual_vs_quarter_sum_mismatch': len(fin_mismatch),
        'fin_snapshot_gap_quarter': len(fin_snapshot_gap),
        'cf_anomaly_quarter': len(cf_anom_q),
        'cf_annual_vs_quarter_sum_mismatch': len(cf_mismatch),
        'cf_snapshot_gap_quarter': len(cf_snapshot_gap),
        'eps_bps_mismatch_annual_vs_fnguide_snapshot': len(eps_bps_mismatch),
      },
      'output_files': {k:str(v) for k,v in paths.items()},
      'baseline_metrics_600': {
        'revenue_match_rate_pct': 100.0,
        'operating_profit_match_rate_pct': 99.83,
        'net_income_match_rate_pct': 96.91,
        'eps_financial_vs_fnguide_main_match_rate_pct': 8.10,
        'bps_financial_vs_fnguide_main_match_rate_pct': 33.56,
        'per_stock_universe_vs_naver_match_rate_pct': 65.67,
        'pbr_stock_universe_vs_naver_match_rate_pct': 68.19,
        'eps_stock_universe_vs_naver_match_rate_pct': 99.83,
      },
      'root_cause_hypothesis': [
        '2024 분기 financial_data/cash_flow_data에 data_source NULL legacy 레코드가 잔존하고, 동일 key의 fnguide snapshot이 없어 갱신되지 않음',
        'fnguide 수집 파이프라인이 2025 Q1~Q3 중심으로 적재되어 2024 분기 키 커버리지가 공백',
        '일부 종목의 Q4 보정/누적-증분 변환 이력으로 Q3 급락+Q4 급반등 패턴이 비정상적으로 남아 있음',
      ]
    }

    jpath=OUT/f'audit_summary_{ts}.json'
    jpath.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':
    run()
