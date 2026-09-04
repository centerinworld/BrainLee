#!/usr/bin/env python3
"""
Audit Claude-added business signal data:
- material purchase / raw material cost
- order backlog
- segment revenue

Outputs JSON evidence for handoff documentation. This script is read-only.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd


ROOT = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime")
DB = ROOT / "stock.db"
OUT = ROOT / "research_outputs" / "claude_business_data_audit.json"


STANDARD_IS_NAMES = {
    "매출액", "수익", "영업수익", "매출", "순매출액", "총매출액", "순영업수익",
    "매출원가", "매출총이익", "매출총이익(손실)",
    "판매비와관리비", "판관비", "영업비용", "판매비및관리비", "판매관리비",
    "영업이익", "영업이익(손실)", "영업손익",
    "금융수익", "금융비용", "금융원가", "이자수익", "이자비용",
    "기타이익", "기타손실", "기타수익", "기타비용", "기타영업외손익",
    "기타영업외수익", "기타영업수익",
    "지분법이익", "지분법손실", "관계기업손익", "지분법적용손익",
    "세전이익", "법인세비용차감전순이익", "세전계속사업이익",
    "법인세비용", "법인세", "소득세비용",
    "당기순이익", "당기순손실", "당기순이익(손실)", "연결당기순이익",
    "지배기업소유주귀속", "비지배지분귀속", "지배주주귀속순이익",
    "기타포괄손익", "총포괄손익", "포괄손익",
    "계속영업손익", "중단영업손익", "희석주당이익", "기본주당이익",
    "감가상각비", "무형자산상각비", "연구개발비", "대손상각비",
}


def q(con: sqlite3.Connection, sql: str, params: tuple = ()) -> pd.DataFrame:
    return pd.read_sql_query(sql, con, params=params)


def records(df: pd.DataFrame, n: int = 20) -> list[dict]:
    return df.head(n).where(pd.notna(df), None).to_dict(orient="records")


def table_summary(con: sqlite3.Connection, table: str, stock_col: str = "stock_code") -> dict:
    row = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    stocks = con.execute(f"SELECT COUNT(DISTINCT {stock_col}) FROM {table}").fetchone()
    return {"rows": int(row[0] or 0), "stocks": int(stocks[0] or 0)}


def audit_material(con: sqlite3.Connection) -> dict:
    summary = {
        "dart_material_purchase": table_summary(con, "dart_material_purchase"),
        "cost_structure": table_summary(con, "cost_structure"),
        "dart_cost_quarterly": table_summary(con, "dart_cost_quarterly"),
    }
    yearly = q(
        con,
        """
        SELECT year, COUNT(*) rows, COUNT(DISTINCT stock_code) stocks,
               SUM(material_purchase_krw IS NOT NULL) non_null
        FROM dart_material_purchase
        GROUP BY year
        ORDER BY year
        """,
    )
    outliers = q(
        con,
        """
        SELECT stock_code, year, material_purchase_krw, unit_label, rcept_no
        FROM dart_material_purchase
        WHERE material_purchase_krw < 1000000000
           OR material_purchase_krw > 50000000000000
        ORDER BY material_purchase_krw DESC
        LIMIT 30
        """,
    )
    ratio_mismatch = q(
        con,
        """
        SELECT stock_code, stock_name, year, quarter, raw_material_cost, revenue,
               raw_material_ratio,
               CASE WHEN revenue > 0 THEN raw_material_cost / revenue ELSE NULL END AS expected_ratio,
               ABS(raw_material_ratio - raw_material_cost / revenue) AS abs_diff
        FROM cost_structure
        WHERE raw_material_cost IS NOT NULL
          AND revenue IS NOT NULL
          AND revenue > 0
          AND raw_material_ratio IS NOT NULL
          AND ABS(raw_material_ratio - raw_material_cost / revenue) > 0.001
        ORDER BY abs_diff DESC
        LIMIT 30
        """,
    )
    qtr_bad_parse = q(
        con,
        """
        SELECT stock_code, fiscal_year, fiscal_quarter, material_cost_krw, confidence,
               source_report_nm, substr(source_excerpt,1,180) AS excerpt
        FROM dart_cost_quarterly
        WHERE material_cost_krw IS NOT NULL
          AND (material_cost_krw BETWEEN 1900 AND 2030 OR material_cost_krw < 10000000)
        ORDER BY fiscal_year DESC, fiscal_quarter DESC
        LIMIT 30
        """,
    )
    return {
        "summary": summary,
        "yearly_coverage": records(yearly, 20),
        "annual_purchase_outliers": records(outliers),
        "cost_structure_ratio_mismatch": records(ratio_mismatch),
        "quarterly_material_likely_bad_parse": records(qtr_bad_parse),
        "verdict": {
            "annual_material_purchase": "usable_with_sanity_filters",
            "cost_structure_annual_backfill": "mostly_usable",
            "dart_cost_quarterly_material": "not_safe_without_reparser",
        },
    }


def audit_backlog(con: sqlite3.Connection) -> dict:
    summary = {
        "order_backlog": table_summary(con, "order_backlog"),
        "dart_backlog_quarterly": table_summary(con, "dart_backlog_quarterly"),
    }
    yearly = q(
        con,
        """
        SELECT year, quarter, COUNT(*) rows, COUNT(DISTINCT stock_code) stocks,
               SUM(backlog_amount IS NOT NULL) non_null_amount,
               SUM(backlog_to_rev IS NOT NULL) non_null_to_rev
        FROM order_backlog
        GROUP BY year, quarter
        ORDER BY year, quarter
        """,
    )
    likely_bad = q(
        con,
        """
        SELECT stock_code, fiscal_year, fiscal_quarter, backlog_amount,
               backlog_unit, backlog_amount_krw, backlog_confidence,
               substr(source_excerpt,1,200) AS excerpt
        FROM dart_backlog_quarterly
        WHERE backlog_amount_krw IS NOT NULL
          AND (
            backlog_amount_krw < 10000000
            OR backlog_amount_krw BETWEEN 1900 AND 2030
            OR lower(source_excerpt) LIKE '%의미가 없습니다%'
            OR source_excerpt LIKE '%해당사항 없음%'
            OR source_excerpt LIKE '%해당 사항 없음%'
          )
        ORDER BY fiscal_year DESC, fiscal_quarter DESC
        LIMIT 40
        """,
    )
    normalized_mismatch = q(
        con,
        """
        SELECT stock_code, stock_name, year, quarter, backlog_amount, backlog_normalized,
               backlog_amount / 1000000.0 AS expected_million,
               ABS(backlog_normalized - backlog_amount / 1000000.0) AS abs_diff,
               data_source
        FROM order_backlog
        WHERE backlog_amount IS NOT NULL
          AND backlog_normalized IS NOT NULL
          AND ABS(backlog_normalized - backlog_amount / 1000000.0) > 1
        ORDER BY abs_diff DESC
        LIMIT 40
        """,
    )
    extreme_to_rev = q(
        con,
        """
        SELECT stock_code, stock_name, year, quarter, backlog_amount, revenue_base,
               backlog_to_rev, data_source
        FROM order_backlog
        WHERE backlog_to_rev IS NOT NULL
          AND (backlog_to_rev < 0 OR backlog_to_rev > 30)
        ORDER BY backlog_to_rev DESC
        LIMIT 40
        """,
    )
    return {
        "summary": summary,
        "year_quarter_coverage": records(yearly, 40),
        "likely_bad_parse": records(likely_bad),
        "normalized_mismatch": records(normalized_mismatch),
        "extreme_backlog_to_revenue": records(extreme_to_rev),
        "verdict": {
            "order_backlog": "unsafe_until_bad_parses_filtered_and_units_rebuilt",
            "dart_backlog_quarterly": "contains_source_evidence_but_parser_needs_harder_table_extraction",
        },
    }


def audit_segment_revenue(con: sqlite3.Connection) -> dict:
    summary = {"segment_revenue": table_summary(con, "segment_revenue")}
    yearly = q(
        con,
        """
        SELECT year, quarter, COUNT(*) rows, COUNT(DISTINCT stock_code) stocks,
               SUM(CASE WHEN segment_name='연결전체' THEN 1 ELSE 0 END) total_rows,
               SUM(CASE WHEN segment_name!='연결전체' THEN 1 ELSE 0 END) breakdown_rows
        FROM segment_revenue
        GROUP BY year, quarter
        ORDER BY year, quarter
        """,
    )
    bad_names = q(
        con,
        """
        SELECT stock_code, year, quarter, segment_name, revenue, report_type
        FROM segment_revenue
        WHERE segment_name IN ({})
        ORDER BY year DESC, quarter DESC, stock_code
        LIMIT 80
        """.format(",".join(["?"] * len(STANDARD_IS_NAMES))),
        tuple(sorted(STANDARD_IS_NAMES)),
    )
    revenue_compare = q(
        con,
        """
        WITH seg_total AS (
          SELECT stock_code, year, quarter, SUM(revenue) AS seg_revenue
          FROM segment_revenue
          WHERE segment_name = '연결전체'
          GROUP BY stock_code, year, quarter
        ),
        fin AS (
          SELECT stock_code, year, quarter, revenue AS fin_revenue
          FROM financial_data
          WHERE revenue IS NOT NULL
        )
        SELECT st.stock_code, st.year, st.quarter, st.seg_revenue,
               f.fin_revenue,
               CASE
                 WHEN f.fin_revenue > 0 THEN st.seg_revenue * 100000000.0 / f.fin_revenue
                 ELSE NULL
               END AS ratio_if_segment_is_eok
        FROM seg_total st
        JOIN fin f
          ON f.stock_code = st.stock_code
         AND f.year = st.year
         AND f.quarter = st.quarter
        WHERE f.fin_revenue > 0
          AND (st.seg_revenue * 100000000.0 / f.fin_revenue < 0.7
               OR st.seg_revenue * 100000000.0 / f.fin_revenue > 1.3)
        ORDER BY ABS(st.seg_revenue * 100000000.0 / f.fin_revenue - 1) DESC
        LIMIT 40
        """,
    )
    mixed_units = q(
        con,
        """
        SELECT stock_code, year, quarter, segment_name, revenue, report_type
        FROM segment_revenue
        WHERE segment_name != '연결전체'
          AND revenue > 100000000000
        ORDER BY revenue DESC
        LIMIT 40
        """,
    )
    return {
        "summary": summary,
        "year_quarter_coverage": records(yearly, 40),
        "standard_income_statement_names_saved_as_segments": records(bad_names, 80),
        "consolidated_total_vs_financial_mismatch": records(revenue_compare),
        "breakdown_rows_likely_krw_mixed_with_eok": records(mixed_units),
        "verdict": {
            "segment_revenue": "not_safe_for_factor_use",
            "reason": "contains consolidated totals, income statement accounts as fake segments, and mixed units",
        },
    }


def main() -> None:
    OUT.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    result = {
        "generated_at": pd.Timestamp.now(tz="Asia/Seoul").isoformat(),
        "database": str(DB),
        "material": audit_material(con),
        "backlog": audit_backlog(con),
        "segment_revenue": audit_segment_revenue(con),
    }
    con.close()
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
