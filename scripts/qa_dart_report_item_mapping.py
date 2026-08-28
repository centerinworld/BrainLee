"""
QA DART report-item mapping quality while collection is running.

This script does not stop collection or rewrite raw source rows. It creates a
review table with suspicious mappings so the raw account_id/account_nm remains
auditable and downstream canonical aggregation can avoid risky rows.

Usage:
  python3 scripts/qa_dart_report_item_mapping.py
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path


DB_PATH = Path("/Applications/stock_dashboard/stock.db")


def norm(text: str | None) -> str:
    return re.sub(r"\s+", "", str(text or "").lower())


RULES: dict[str, dict[str, tuple[str, ...]]] = {
    "trade_receivable": {
        "must": ("매출채권", "외상매출금", "수취채권"),
        "bad": ("대손", "충당금", "매입채무", "미지급", "대여금"),
    },
    "trade_payable": {
        "must": ("매입채무", "외상매입금", "지급채무"),
        "bad": ("매출채권", "미수금", "대손", "충당금"),
    },
    "inventory_assets": {
        "must": ("재고", "상품", "제품", "재공품", "원재료", "저장품"),
        "bad": ("평가손실", "감모손실", "매출원가", "충당금"),
    },
    "short_term_borrowings": {
        "must": ("단기차입", "유동성", "유동차입", "유동사채"),
        "bad": ("비유동",),
    },
    "long_term_borrowings": {
        "must": ("장기차입", "비유동차입", "사채"),
        "bad": ("단기차입", "유동성장기", "유동사채"),
    },
    "contract_assets": {
        "must": ("계약자산", "미청구공사"),
        "bad": ("계약부채", "초과청구"),
    },
    "contract_liabilities": {
        "must": ("계약부채", "초과청구공사"),
        "bad": ("계약자산", "미청구"),
    },
    "capex_ppe_purchase": {
        "must": ("유형자산", "설비"),
        "bad": ("처분", "매각", "감소"),
    },
    "capex_intangible_purchase": {
        "must": ("무형자산", "개발비"),
        "bad": ("처분", "매각", "감소"),
    },
    "research_development_expense": {
        "must": ("연구", "개발"),
        "bad": ("자산", "무형자산", "상각", "손상"),
    },
    "depreciation_amortization_expense": {
        "must": ("감가상각", "상각"),
        "bad": ("누계", "대손", "대손상각", "기타의대손", "대손충당"),
    },
    "provisions": {
        "must": ("충당부채", "충당금"),
        "bad": ("대손", "평가손실"),
    },
}


def init_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dart_report_item_mapping_qa (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            fiscal_year INTEGER NOT NULL,
            fiscal_quarter INTEGER NOT NULL,
            fs_div TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            account_id TEXT,
            account_nm TEXT,
            value REAL,
            issue_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            detail TEXT,
            reviewed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (
                stock_code, fiscal_year, fiscal_quarter, fs_div,
                metric_name, account_id, account_nm, issue_type
            )
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_drimq_issue
        ON dart_report_item_mapping_qa(issue_type, severity, reviewed)
        """
    )
    conn.commit()


def add_issue(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    issue_type: str,
    severity: str,
    detail: str,
) -> int:
    before = conn.total_changes
    conn.execute(
        """
        INSERT OR IGNORE INTO dart_report_item_mapping_qa
        (stock_code, fiscal_year, fiscal_quarter, fs_div, metric_name,
         account_id, account_nm, value, issue_type, severity, detail)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            row["stock_code"],
            row["fiscal_year"],
            row["fiscal_quarter"],
            row["fs_div"],
            row["metric_name"],
            row["account_id"],
            row["account_nm"],
            row["value"],
            issue_type,
            severity,
            detail,
        ),
    )
    return conn.total_changes - before


def rule_based_qa(conn: sqlite3.Connection) -> int:
    added = 0
    rows = conn.execute(
        """
        SELECT stock_code, fiscal_year, fiscal_quarter, fs_div, metric_name,
               account_id, account_nm, value
        FROM dart_report_items_quarterly
        """
    ).fetchall()
    for row in rows:
        metric = row["metric_name"]
        rule = RULES.get(metric)
        if not rule:
            continue
        name = norm(row["account_nm"])
        bad_hits = [x for x in rule.get("bad", ()) if norm(x) in name]
        if bad_hits:
            added += add_issue(
                conn,
                row,
                "excluded_keyword_hit",
                "HIGH",
                f"metric={metric} account_nm includes excluded keywords: {', '.join(bad_hits)}",
            )
            continue
        must = rule.get("must", ())
        if must and not any(norm(x) in name for x in must):
            # Standard account_id rows can be valid even when Korean name is broad.
            severity = "MEDIUM" if str(row["account_id"] or "").startswith(("ifrs-full_", "dart_")) else "HIGH"
            added += add_issue(
                conn,
                row,
                "missing_expected_keyword",
                severity,
                f"metric={metric} account_nm lacks expected keywords: {', '.join(must)}",
            )
    return added


def duplicate_account_qa(conn: sqlite3.Connection) -> int:
    added = 0
    groups = conn.execute(
        """
        SELECT stock_code, fiscal_year, fiscal_quarter, fs_div,
               COALESCE(account_id, '') AS account_id,
               COALESCE(account_nm, '') AS account_nm,
               COUNT(DISTINCT metric_name) AS metric_cnt,
               GROUP_CONCAT(DISTINCT metric_name) AS metrics
        FROM dart_report_items_quarterly
        GROUP BY stock_code, fiscal_year, fiscal_quarter, fs_div,
                 COALESCE(account_id, ''), COALESCE(account_nm, '')
        HAVING COUNT(DISTINCT metric_name) > 1
        """
    ).fetchall()
    for group in groups:
        rows = conn.execute(
            """
            SELECT stock_code, fiscal_year, fiscal_quarter, fs_div, metric_name,
                   account_id, account_nm, value
            FROM dart_report_items_quarterly
            WHERE stock_code=? AND fiscal_year=? AND fiscal_quarter=? AND fs_div=?
              AND COALESCE(account_id, '')=? AND COALESCE(account_nm, '')=?
            """,
            (
                group["stock_code"],
                group["fiscal_year"],
                group["fiscal_quarter"],
                group["fs_div"],
                group["account_id"],
                group["account_nm"],
            ),
        ).fetchall()
        for row in rows:
            added += add_issue(
                conn,
                row,
                "same_account_multiple_metrics",
                "HIGH",
                f"same account mapped to multiple metrics: {group['metrics']}",
            )
    return added


def extreme_value_qa(conn: sqlite3.Connection) -> int:
    added = 0
    rows = conn.execute(
        """
        WITH rev AS (
          SELECT stock_code, year AS fiscal_year, quarter AS fiscal_quarter,
                 MAX(ABS(revenue)) AS revenue
          FROM financial_data
          WHERE revenue IS NOT NULL AND ABS(revenue) > 0
          GROUP BY stock_code, year, quarter
        )
        SELECT d.stock_code, d.fiscal_year, d.fiscal_quarter, d.fs_div, d.metric_name,
               d.account_id, d.account_nm, d.value, r.revenue,
               ABS(d.value) / r.revenue AS value_to_revenue
        FROM dart_report_items_quarterly d
        JOIN rev r ON r.stock_code=d.stock_code
                  AND r.fiscal_year=d.fiscal_year
                  AND r.fiscal_quarter=d.fiscal_quarter
        WHERE d.metric_name IN (
            'trade_receivable', 'inventory_assets', 'trade_payable',
            'contract_assets', 'contract_liabilities', 'advances_received'
        )
          AND ABS(d.value) / r.revenue > 20
        """
    ).fetchall()
    for row in rows:
        added += add_issue(
            conn,
            row,
            "extreme_value_to_revenue",
            "MEDIUM",
            f"value/revenue={row['value_to_revenue']:.2f}; may be valid for financials but needs review",
        )
    return added


def main() -> int:
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    init_table(conn)

    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[START] DART report item mapping QA {started}", flush=True)
    added = 0
    added += rule_based_qa(conn)
    added += duplicate_account_qa(conn)
    added += extreme_value_qa(conn)
    conn.commit()

    print(f"new_issues={added}", flush=True)
    for row in conn.execute(
        """
        SELECT severity, issue_type, COUNT(*) AS cnt
        FROM dart_report_item_mapping_qa
        WHERE reviewed=0
        GROUP BY severity, issue_type
        ORDER BY CASE severity WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END,
                 cnt DESC
        """
    ):
        print(f"{row['severity']} {row['issue_type']}: {row['cnt']}", flush=True)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
