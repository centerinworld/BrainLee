#!/usr/bin/env python3
"""Audit reported half-year filings against loaded quarterly financial data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import engine


ELIGIBLE_CTE = """
WITH latest_universe AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY stock_code
        ORDER BY base_date DESC NULLS LAST, id DESC
    ) AS rn
    FROM stock_universe
), eligible AS (
    SELECT stock_code, stock_name, market
    FROM latest_universe
    WHERE rn = 1
      AND COALESCE(stock_type, '보통주') = '보통주'
      AND market IN ('유가증권', '코스피', '코스닥', 'KOSPI', 'KOSDAQ')
      AND stock_code ~ '^[0-9]{6}$'
      AND COALESCE(stock_name, '') NOT LIKE '%ETF%'
      AND COALESCE(stock_name, '') NOT LIKE '%ETN%'
      AND EXISTS (
          SELECT 1
          FROM price_history p
          WHERE p.stock_code = latest_universe.stock_code
            AND p.date::date >= (
                SELECT MAX(date::date) - INTERVAL '30 days' FROM price_history
            )
      )
)
"""


def _rows(conn, sql: str, params: dict | None = None) -> list[dict]:
    return [dict(row) for row in conn.execute(text(sql), params or {}).mappings().all()]


def build_report(year: int, quarter: int) -> dict:
    if quarter != 2:
        raise ValueError("This audit currently supports half-year (Q2) filings only")

    period_label = f"({year}.06)"
    params = {"year": year, "quarter": quarter, "period_label": f"%{period_label}%"}

    with engine.connect() as conn:
        universe = _rows(conn, ELIGIBLE_CTE + "SELECT COUNT(*) AS stocks FROM eligible")[0]
        source_coverage = _rows(conn, """
            SELECT report_type, data_source, COUNT(*) AS rows,
                   COUNT(DISTINCT stock_code) AS stocks
            FROM financial_data
            WHERE year = :year AND quarter = :quarter AND is_annual = FALSE
            GROUP BY report_type, data_source
            ORDER BY report_type, data_source
        """, params)
        period_coverage = _rows(conn, """
            SELECT year, quarter, COUNT(DISTINCT stock_code) AS stocks,
                   COUNT(DISTINCT stock_code) FILTER (
                       WHERE revenue IS NOT NULL
                         AND operating_profit IS NOT NULL
                         AND net_income IS NOT NULL
                   ) AS core_income_statement,
                   COUNT(DISTINCT stock_code) FILTER (
                       WHERE total_assets IS NOT NULL AND total_equity IS NOT NULL
                   ) AS core_balance_sheet
            FROM financial_data
            WHERE is_annual = FALSE
              AND (year, quarter) IN ((:year - 1, 2), (:year, 1), (:year, 2))
            GROUP BY year, quarter
            ORDER BY year, quarter
        """, params)
        filing_match = _rows(conn, ELIGIBLE_CTE + """
            , filed AS (
                SELECT DISTINCT d.stock_code
                FROM dart_disclosures d
                JOIN eligible e USING (stock_code)
                WHERE d.report_nm LIKE '%반기보고서%'
                  AND d.report_nm LIKE :period_label
            ), loaded AS (
                SELECT DISTINCT stock_code
                FROM financial_data
                WHERE year = :year AND quarter = :quarter AND is_annual = FALSE
            )
            SELECT (SELECT COUNT(*) FROM filed) AS filed,
                   (SELECT COUNT(*) FROM filed JOIN loaded USING (stock_code)) AS loaded,
                   (SELECT COUNT(*) FROM filed LEFT JOIN loaded USING (stock_code)
                    WHERE loaded.stock_code IS NULL) AS missing
        """, params)[0]
        missing_filed = _rows(conn, ELIGIBLE_CTE + """
            , filed AS (
                SELECT DISTINCT d.stock_code
                FROM dart_disclosures d
                JOIN eligible e USING (stock_code)
                WHERE d.report_nm LIKE '%반기보고서%'
                  AND d.report_nm LIKE :period_label
            ), loaded AS (
                SELECT DISTINCT stock_code
                FROM financial_data
                WHERE year = :year AND quarter = :quarter AND is_annual = FALSE
            )
            SELECT e.stock_code, e.stock_name, e.market
            FROM filed f
            JOIN eligible e USING (stock_code)
            LEFT JOIN loaded l USING (stock_code)
            WHERE l.stock_code IS NULL
            ORDER BY e.market, e.stock_code
        """, params)
        null_profile = _rows(conn, """
            SELECT COUNT(*) AS rows,
                   COUNT(*) FILTER (WHERE revenue IS NULL) AS revenue_null,
                   COUNT(*) FILTER (WHERE operating_profit IS NULL) AS operating_profit_null,
                   COUNT(*) FILTER (WHERE net_income IS NULL) AS net_income_null,
                   COUNT(*) FILTER (WHERE total_assets IS NULL) AS total_assets_null,
                   COUNT(*) FILTER (WHERE total_equity IS NULL) AS total_equity_null,
                   COUNT(*) FILTER (WHERE eps IS NULL) AS eps_null
            FROM financial_data
            WHERE year = :year AND quarter = :quarter AND is_annual = FALSE
              AND data_source = 'dart_q2_verified'
        """, params)[0]
        duplicate_profile = _rows(conn, """
            SELECT COUNT(*) AS duplicate_groups, COALESCE(SUM(n - 1), 0) AS extra_rows
            FROM (
                SELECT stock_code, year, quarter, is_annual, report_type,
                       COUNT(*) AS n
                FROM financial_data
                WHERE year = :year AND quarter = :quarter AND is_annual = FALSE
                GROUP BY 1, 2, 3, 4, 5
                HAVING COUNT(*) > 1
            ) duplicates
        """, params)[0]
        balance_identity = _rows(conn, """
            SELECT COUNT(*) AS checked,
                   COUNT(*) FILTER (
                       WHERE ABS(total_assets - total_liabilities - total_equity)
                             > GREATEST(ABS(total_assets) * 0.02, 1000000)
                   ) AS failed
            FROM financial_data
            WHERE year = :year AND quarter = :quarter AND is_annual = FALSE
              AND data_source = 'dart_q2_verified'
              AND total_assets IS NOT NULL
              AND total_liabilities IS NOT NULL
              AND total_equity IS NOT NULL
        """, params)[0]
        cashflow = _rows(conn, """
            SELECT COUNT(DISTINCT stock_code) AS stocks,
                   COUNT(DISTINCT stock_code) FILTER (WHERE operating_cf IS NOT NULL) AS operating_cf,
                   COUNT(DISTINCT stock_code) FILTER (
                       WHERE capex IS NOT NULL OR capex_q IS NOT NULL
                   ) AS capex
            FROM cash_flow_data
            WHERE year = :year AND quarter = :quarter AND is_annual = FALSE
              AND data_source = 'dart_q2_verified'
        """, params)[0]

    return {
        "database": engine.dialect.name,
        "year": year,
        "quarter": quarter,
        "eligible_universe": universe,
        "source_coverage": source_coverage,
        "period_coverage": period_coverage,
        "dart_half_year_filing_match": filing_match,
        "missing_filed_stocks": missing_filed,
        "null_profile": null_profile,
        "duplicate_profile": duplicate_profile,
        "balance_identity": balance_identity,
        "cashflow_coverage": cashflow,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--quarter", type=int, default=2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_report(args.year, args.quarter)
    payload = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    print(payload)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
