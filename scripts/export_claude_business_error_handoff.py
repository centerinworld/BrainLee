#!/usr/bin/env python3
"""Export row-level error handoff files for Claude's business-signal backfill work.

This script is intentionally read-only against stock.db. It writes CSV/JSON evidence
under research_outputs so another agent can repair parsers and data without needing
to rediscover the failure modes.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "stock.db"
OUT_DIR = ROOT / "research_outputs" / "claude_business_error_handoff_20260619"
KST = timezone(timedelta(hours=9))


NO_METRIC_RE = re.compile(
    r"(해당\s*사항\s*(없|없습|없음)|의미가\s*없|수주잔고는\s*의미가\s*없|"
    r"수주\s*잔고.*없|잔고.*없음)",
    re.IGNORECASE,
)
DATE_OR_NON_AMOUNT_RE = re.compile(
    r"(20[0-3]\d\s*[년./-]|20[0-3]\d\s*년|제\s*\d+\s*기|\d+(?:\.\d+)?\s*%|"
    r"\d{4}\.\d{1,2}|\d{4}-\d{1,2}-\d{1,2})"
)

STANDARD_IS_NAMES = {
    "매출액",
    "수익(매출액)",
    "영업수익",
    "기타수익",
    "기타영업수익",
    "기타영업외수익",
    "금융수익",
    "이자수익",
    "배당금수익",
    "외환차익",
    "외화환산이익",
}


def rows(con: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    con.row_factory = sqlite3.Row
    return [dict(r) for r in con.execute(sql, params).fetchall()]


def scalar(con: sqlite3.Connection, sql: str) -> Any:
    return con.execute(sql).fetchone()[0]


def write_csv(name: str, data: list[dict[str, Any]]) -> str:
    path = OUT_DIR / name
    if not data:
        path.write_text("", encoding="utf-8")
        return str(path)
    cols = list(data[0].keys())
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        writer.writerows(data)
    return str(path)


def with_category(data: list[dict[str, Any]], category: str) -> list[dict[str, Any]]:
    return [{"error_category": category, **r} for r in data]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    summary: dict[str, Any] = {
        "generated_at": datetime.now(KST).isoformat(),
        "database": str(DB_PATH),
        "output_dir": str(OUT_DIR),
        "note": "Read-only audit. CSV files contain row-level evidence for Claude handoff.",
        "tables": {},
        "error_counts": {},
        "files": {},
    }

    for table in [
        "dart_material_purchase",
        "cost_structure",
        "dart_cost_quarterly",
        "order_backlog",
        "dart_backlog_quarterly",
        "segment_revenue",
    ]:
        summary["tables"][table] = {
            "rows": scalar(con, f"SELECT COUNT(*) FROM {table}"),
            "stocks": scalar(con, f"SELECT COUNT(DISTINCT stock_code) FROM {table}"),
        }

    # 1) Material purchase and cost_structure.
    material_annual_outliers = rows(
        con,
        """
        SELECT stock_code, year, report_type, material_purchase_krw, unit_label, rcept_no, collected_at
        FROM dart_material_purchase
        WHERE material_purchase_krw IS NOT NULL
          AND (material_purchase_krw < 1000000000 OR material_purchase_krw > 50000000000000)
        ORDER BY ABS(material_purchase_krw) DESC
        """,
    )

    cost_ratio_mismatch = rows(
        con,
        """
        SELECT stock_code, stock_name, year, quarter, raw_material_cost, revenue,
               raw_material_ratio,
               raw_material_cost / revenue AS expected_ratio,
               ABS(raw_material_ratio - (raw_material_cost / revenue)) AS abs_diff,
               data_source
        FROM cost_structure
        WHERE raw_material_cost IS NOT NULL
          AND revenue IS NOT NULL
          AND revenue > 0
          AND raw_material_ratio IS NOT NULL
          AND ABS(raw_material_ratio - (raw_material_cost / revenue)) > 0.05
        ORDER BY abs_diff DESC
        """,
    )

    cost_ratio_out_of_range = rows(
        con,
        """
        SELECT stock_code, stock_name, year, quarter, raw_material_cost, revenue,
               raw_material_ratio, data_source
        FROM cost_structure
        WHERE raw_material_ratio IS NOT NULL
          AND (raw_material_ratio < 0 OR raw_material_ratio > 3)
        ORDER BY raw_material_ratio DESC
        """,
    )

    quarterly_material_bad = rows(
        con,
        """
        SELECT stock_code, fiscal_year, fiscal_quarter, report_type, material_cost_krw,
               confidence, source_report_nm, source_rcept_dt, parser_version,
               SUBSTR(source_excerpt, 1, 240) AS excerpt
        FROM dart_cost_quarterly
        WHERE material_cost_krw IS NOT NULL
          AND (
            material_cost_krw <= 0
            OR material_cost_krw < 10000000
            OR confidence < 0.75
          )
        ORDER BY fiscal_year DESC, fiscal_quarter DESC, material_cost_krw ASC
        """,
    )
    # Add parser-context flags for rows where value size alone may not show the problem.
    quarterly_context_bad = []
    for r in rows(
        con,
        """
        SELECT stock_code, fiscal_year, fiscal_quarter, report_type, material_cost_krw,
               confidence, source_report_nm, source_rcept_dt, parser_version,
               SUBSTR(source_excerpt, 1, 300) AS excerpt
        FROM dart_cost_quarterly
        WHERE material_cost_krw IS NOT NULL
        """,
    ):
        excerpt = r.get("excerpt") or ""
        if DATE_OR_NON_AMOUNT_RE.search(excerpt):
            quarterly_context_bad.append(r)

    # 2) Backlog.
    backlog_bad_parse = []
    for r in rows(
        con,
        """
        SELECT stock_code, fiscal_year, fiscal_quarter, report_type, backlog_amount,
               backlog_unit, backlog_amount_krw, backlog_confidence,
               source_report_nm, source_rcept_dt, parser_version,
               SUBSTR(source_excerpt, 1, 300) AS excerpt
        FROM dart_backlog_quarterly
        WHERE backlog_amount IS NOT NULL OR backlog_amount_krw IS NOT NULL
        ORDER BY fiscal_year DESC, fiscal_quarter DESC
        """,
    ):
        excerpt = r.get("excerpt") or ""
        amount = r.get("backlog_amount")
        amount_krw = r.get("backlog_amount_krw")
        flags = []
        if amount is not None and amount <= 0:
            flags.append("non_positive_amount")
        if amount_krw is not None and 0 < amount_krw < 10000000:
            flags.append("tiny_amount_krw")
        if amount is not None and 1900 <= float(amount) <= 2035:
            flags.append("date_like_amount")
        if NO_METRIC_RE.search(excerpt):
            flags.append("no_metric_phrase")
        if DATE_OR_NON_AMOUNT_RE.search(excerpt):
            flags.append("date_or_percent_context")
        if flags:
            backlog_bad_parse.append({"flags": "|".join(flags), **r})

    order_backlog_unit_mismatch = rows(
        con,
        """
        SELECT stock_code, stock_name, year, quarter, report_type, backlog_amount,
               backlog_unit, backlog_normalized,
               backlog_amount / 1000000.0 AS expected_million_krw,
               CASE
                 WHEN backlog_normalized IS NULL OR backlog_amount IS NULL OR backlog_amount = 0 THEN NULL
                 ELSE backlog_normalized / (backlog_amount / 1000000.0)
               END AS normalized_to_expected_ratio,
               data_source, rcept_no
        FROM order_backlog
        WHERE backlog_amount IS NOT NULL
          AND backlog_amount > 0
          AND backlog_normalized IS NOT NULL
          AND ABS(backlog_normalized - backlog_amount / 1000000.0)
              > MAX(1000.0, ABS(backlog_amount / 1000000.0) * 0.05)
        ORDER BY ABS(normalized_to_expected_ratio - 1) DESC
        """,
    )

    order_backlog_ratio_mismatch = rows(
        con,
        """
        SELECT stock_code, stock_name, year, quarter, backlog_amount, revenue_base,
               backlog_to_rev,
               backlog_amount / revenue_base AS expected_backlog_to_rev,
               ABS(backlog_to_rev - (backlog_amount / revenue_base)) AS abs_diff,
               data_source
        FROM order_backlog
        WHERE backlog_amount IS NOT NULL
          AND revenue_base IS NOT NULL
          AND revenue_base > 0
          AND backlog_to_rev IS NOT NULL
          AND ABS(backlog_to_rev - (backlog_amount / revenue_base)) > 0.05
        ORDER BY abs_diff DESC
        """,
    )

    order_backlog_bad_values = rows(
        con,
        """
        SELECT stock_code, stock_name, year, quarter, report_type, backlog_amount,
               backlog_unit, backlog_normalized, new_orders, new_order_amount,
               completion_ratio, revenue_base, backlog_to_rev, data_source, rcept_no
        FROM order_backlog
        WHERE (backlog_amount IS NOT NULL AND backlog_amount <= 0)
           OR (backlog_amount IS NOT NULL AND backlog_amount > 0 AND backlog_amount < 10000000)
           OR (completion_ratio IS NOT NULL AND (completion_ratio < 0 OR completion_ratio > 1.5))
           OR (new_orders IS NOT NULL AND new_orders < 0)
           OR (new_order_amount IS NOT NULL AND new_order_amount < 0)
        ORDER BY year DESC, quarter DESC
        """,
    )

    # 3) Segment revenue.
    placeholders = ",".join(["?"] * len(STANDARD_IS_NAMES))
    segment_fake_is = rows(
        con,
        f"""
        SELECT stock_code, corp_code, year, quarter, segment_name, revenue,
               operating_profit, assets, revenue_pct, report_type, rcept_no
        FROM segment_revenue
        WHERE segment_name IN ({placeholders})
        ORDER BY year DESC, stock_code
        """,
        tuple(sorted(STANDARD_IS_NAMES)),
    )

    segment_breakdown_rows = rows(
        con,
        """
        SELECT stock_code, corp_code, year, quarter, segment_name, revenue,
               operating_profit, assets, revenue_pct, report_type, rcept_no
        FROM segment_revenue
        WHERE segment_name IS NOT NULL
          AND segment_name != '연결전체'
        ORDER BY year DESC, stock_code, segment_name
        """,
    )

    segment_consolidated_mismatch = rows(
        con,
        """
        WITH joined AS (
          SELECT s.stock_code, s.year, s.quarter, s.segment_name,
                 s.revenue AS segment_revenue,
                 f.revenue AS financial_revenue,
                 CASE WHEN f.revenue IS NULL OR f.revenue = 0 THEN NULL
                      ELSE s.revenue / f.revenue END AS raw_ratio,
                 CASE WHEN f.revenue IS NULL OR f.revenue = 0 THEN NULL
                      ELSE s.revenue * 100000000.0 / f.revenue END AS eok_to_krw_ratio,
                 CASE WHEN f.revenue IS NULL OR f.revenue = 0 THEN NULL
                      ELSE s.revenue / 100000000.0 / f.revenue END AS krw_to_eok_ratio
          FROM segment_revenue s
          JOIN financial_data f
            ON f.stock_code = s.stock_code
           AND f.year = s.year
           AND f.quarter = s.quarter
          WHERE s.segment_name = '연결전체'
            AND s.revenue IS NOT NULL
            AND f.revenue IS NOT NULL
            AND f.revenue != 0
        )
        SELECT *
        FROM joined
        WHERE NOT (
             raw_ratio BETWEEN 0.8 AND 1.2
          OR eok_to_krw_ratio BETWEEN 0.8 AND 1.2
          OR krw_to_eok_ratio BETWEEN 0.8 AND 1.2
        )
        ORDER BY ABS(COALESCE(raw_ratio, 0) - 1) DESC
        """,
    )

    # 4) Strategy/source usage routes that must not consume raw dirty tables.
    raw_table_usage: list[dict[str, Any]] = []
    for rel in ["scripts", "routes", "main.py", "tenbagger_engine.py"]:
        path = ROOT / rel
        if path.is_file():
            files = [path]
        elif path.is_dir():
            files = [p for p in path.rglob("*.py") if "__pycache__" not in p.parts]
        else:
            files = []
        for file in files:
            text = file.read_text(encoding="utf-8", errors="ignore")
            for table in ["dart_cost_quarterly", "order_backlog", "dart_backlog_quarterly", "segment_revenue"]:
                if table in text:
                    for lineno, line in enumerate(text.splitlines(), start=1):
                        if table in line:
                            raw_table_usage.append(
                                {
                                    "file": str(file.relative_to(ROOT)),
                                    "line": lineno,
                                    "table": table,
                                    "text": line.strip()[:300],
                                }
                            )

    groups = {
        "material_annual_outliers.csv": with_category(material_annual_outliers, "material_annual_outlier"),
        "cost_structure_ratio_mismatch.csv": with_category(cost_ratio_mismatch, "cost_structure_ratio_mismatch"),
        "cost_structure_ratio_out_of_range.csv": with_category(cost_ratio_out_of_range, "cost_structure_ratio_out_of_range"),
        "dart_cost_quarterly_bad_values.csv": with_category(quarterly_material_bad, "dart_cost_quarterly_bad_value"),
        "dart_cost_quarterly_bad_context.csv": with_category(quarterly_context_bad, "dart_cost_quarterly_bad_context"),
        "dart_backlog_quarterly_bad_parse.csv": with_category(backlog_bad_parse, "dart_backlog_quarterly_bad_parse"),
        "order_backlog_unit_mismatch.csv": with_category(order_backlog_unit_mismatch, "order_backlog_unit_mismatch"),
        "order_backlog_ratio_mismatch.csv": with_category(order_backlog_ratio_mismatch, "order_backlog_ratio_mismatch"),
        "order_backlog_bad_values.csv": with_category(order_backlog_bad_values, "order_backlog_bad_values"),
        "segment_revenue_fake_is_accounts.csv": with_category(segment_fake_is, "segment_revenue_fake_is_account"),
        "segment_revenue_breakdown_rows.csv": with_category(segment_breakdown_rows, "segment_revenue_breakdown_row"),
        "segment_revenue_consolidated_mismatch.csv": with_category(segment_consolidated_mismatch, "segment_revenue_consolidated_mismatch"),
        "raw_table_usage_in_code.csv": with_category(raw_table_usage, "raw_table_usage_in_code"),
    }

    for filename, data in groups.items():
        summary["error_counts"][filename[:-4]] = len(data)
        summary["files"][filename] = write_csv(filename, data)

    # Compact top samples in JSON for quick reading.
    summary["top_samples"] = {
        key[:-4]: value[:10]
        for key, value in groups.items()
        if value
    }

    summary_path = OUT_DIR / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
