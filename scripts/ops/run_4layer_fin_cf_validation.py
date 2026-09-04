#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB = Path('/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db')
OUT = Path('/Volumes/Realtek_NVME/stock_dashboard/runtime/scratch')


def q(conn, sql, args=()):
    return conn.execute(sql, args).fetchone()[0]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB))
    try:
        result = {'generated_at': datetime.now().isoformat(timespec='seconds')}

        # Layer 1: uniqueness / key integrity
        l1 = {
            'financial_dup_key': q(conn, """
                SELECT COUNT(*) FROM (
                  SELECT stock_code,year,quarter,is_annual,report_type,COUNT(*) c
                  FROM financial_data GROUP BY 1,2,3,4,5 HAVING c>1
                )
            """),
            'cashflow_dup_key': q(conn, """
                SELECT COUNT(*) FROM (
                  SELECT stock_code,year,quarter,is_annual,report_type,COUNT(*) c
                  FROM cash_flow_data GROUP BY 1,2,3,4,5 HAVING c>1
                )
            """),
        }

        # Layer 2: core field completeness
        l2 = {
            'financial_core_null_rows': q(conn, """
                SELECT COUNT(*) FROM financial_data
                WHERE report_type='CFS' AND (
                  revenue IS NULL OR operating_profit IS NULL OR net_income IS NULL
                  OR total_assets IS NULL OR total_liabilities IS NULL OR total_equity IS NULL
                )
            """),
            'cashflow_core_null_rows': q(conn, """
                SELECT COUNT(*) FROM cash_flow_data
                WHERE report_type='CFS' AND (
                  operating_cf IS NULL OR investing_cf IS NULL OR financing_cf IS NULL
                )
            """),
        }

        # Layer 3: accounting identities
        l3 = {
            'financial_identity_mismatch': q(conn, """
                SELECT COUNT(*) FROM financial_data
                WHERE report_type='CFS'
                  AND total_assets IS NOT NULL AND total_liabilities IS NOT NULL AND total_equity IS NOT NULL
                  AND ABS((total_liabilities + total_equity) - total_assets) > 1000
            """),
            'financial_suspicious_equal_assets_liab': q(conn, """
                SELECT COUNT(*) FROM financial_data
                WHERE report_type='CFS' AND total_assets IS NOT NULL AND total_liabilities IS NOT NULL
                  AND ABS(total_assets-total_liabilities) < 1
            """),
            'financial_suspicious_equal_assets_equity': q(conn, """
                SELECT COUNT(*) FROM financial_data
                WHERE report_type='CFS' AND total_assets IS NOT NULL AND total_equity IS NOT NULL
                  AND ABS(total_assets-total_equity) < 1
            """),
        }

        # Layer 4: 1:1 source snapshot match (where snapshot exists)
        l4 = {
            'financial_compare_rows': q(conn, """
                WITH latest AS (
                  SELECT stock_code,year,quarter,is_annual,report_type,MAX(fetched_at) mf
                  FROM financial_source_snapshot
                  WHERE report_type='CFS'
                  GROUP BY 1,2,3,4,5
                )
                SELECT COUNT(*)
                FROM financial_data fd
                JOIN latest l
                  ON l.stock_code=fd.stock_code AND l.year=fd.year AND l.quarter=fd.quarter
                 AND l.is_annual=fd.is_annual AND l.report_type=fd.report_type
                JOIN financial_source_snapshot fss
                  ON fss.stock_code=l.stock_code AND fss.year=l.year AND fss.quarter=l.quarter
                 AND fss.is_annual=l.is_annual AND fss.report_type=l.report_type AND fss.fetched_at=l.mf
                WHERE fd.report_type='CFS'
            """),
            'financial_value_mismatch_rows': q(conn, """
                WITH latest AS (
                  SELECT stock_code,year,quarter,is_annual,report_type,MAX(fetched_at) mf
                  FROM financial_source_snapshot
                  WHERE report_type='CFS'
                  GROUP BY 1,2,3,4,5
                )
                SELECT COUNT(*)
                FROM financial_data fd
                JOIN latest l
                  ON l.stock_code=fd.stock_code AND l.year=fd.year AND l.quarter=fd.quarter
                 AND l.is_annual=fd.is_annual AND l.report_type=fd.report_type
                JOIN financial_source_snapshot fss
                  ON fss.stock_code=l.stock_code AND fss.year=l.year AND fss.quarter=l.quarter
                 AND fss.is_annual=l.is_annual AND fss.report_type=l.report_type AND fss.fetched_at=l.mf
                WHERE fd.report_type='CFS' AND (
                    (fd.revenue IS NOT NULL AND fss.revenue IS NOT NULL AND ABS(fd.revenue-fss.revenue)>1000)
                 OR (fd.operating_profit IS NOT NULL AND fss.operating_profit IS NOT NULL AND ABS(fd.operating_profit-fss.operating_profit)>1000)
                 OR (fd.net_income IS NOT NULL AND fss.net_income IS NOT NULL AND ABS(fd.net_income-fss.net_income)>1000)
                 OR (fd.total_assets IS NOT NULL AND fss.total_assets IS NOT NULL AND ABS(fd.total_assets-fss.total_assets)>1000)
                 OR (fd.total_liabilities IS NOT NULL AND fss.total_liabilities IS NOT NULL AND ABS(fd.total_liabilities-fss.total_liabilities)>1000)
                 OR (fd.total_equity IS NOT NULL AND fss.total_equity IS NOT NULL AND ABS(fd.total_equity-fss.total_equity)>1000)
                )
            """),
            'cashflow_compare_rows': q(conn, """
                WITH latest AS (
                  SELECT stock_code,year,quarter,is_annual,report_type,MAX(fetched_at) mf
                  FROM financial_source_snapshot
                  WHERE report_type='CFS'
                  GROUP BY 1,2,3,4,5
                )
                SELECT COUNT(*)
                FROM cash_flow_data cf
                JOIN latest l
                  ON l.stock_code=cf.stock_code AND l.year=cf.year AND l.quarter=cf.quarter
                 AND l.is_annual=cf.is_annual AND l.report_type=cf.report_type
                JOIN financial_source_snapshot fss
                  ON fss.stock_code=l.stock_code AND fss.year=l.year AND fss.quarter=l.quarter
                 AND fss.is_annual=l.is_annual AND fss.report_type=l.report_type AND fss.fetched_at=l.mf
                WHERE cf.report_type='CFS'
            """),
            'cashflow_value_mismatch_rows': q(conn, """
                WITH latest AS (
                  SELECT stock_code,year,quarter,is_annual,report_type,MAX(fetched_at) mf
                  FROM financial_source_snapshot
                  WHERE report_type='CFS'
                  GROUP BY 1,2,3,4,5
                )
                SELECT COUNT(*)
                FROM cash_flow_data cf
                JOIN latest l
                  ON l.stock_code=cf.stock_code AND l.year=cf.year AND l.quarter=cf.quarter
                 AND l.is_annual=cf.is_annual AND l.report_type=cf.report_type
                JOIN financial_source_snapshot fss
                  ON fss.stock_code=l.stock_code AND fss.year=l.year AND fss.quarter=l.quarter
                 AND fss.is_annual=l.is_annual AND fss.report_type=l.report_type AND fss.fetched_at=l.mf
                WHERE cf.report_type='CFS' AND (
                    (cf.operating_cf IS NOT NULL AND fss.operating_cf IS NOT NULL AND ABS(cf.operating_cf-fss.operating_cf)>1000)
                 OR (cf.investing_cf IS NOT NULL AND fss.investing_cf IS NOT NULL AND ABS(cf.investing_cf-fss.investing_cf)>1000)
                 OR (cf.financing_cf IS NOT NULL AND fss.financing_cf IS NOT NULL AND ABS(cf.financing_cf-fss.financing_cf)>1000)
                )
            """),
        }

        result['layer1_key_integrity'] = l1
        result['layer2_completeness'] = l2
        result['layer3_accounting_identity'] = l3
        result['layer4_source_1to1'] = l4

        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        out = OUT / f'four_layer_validation_{ts}.json'
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(out)
    finally:
        conn.close()
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
