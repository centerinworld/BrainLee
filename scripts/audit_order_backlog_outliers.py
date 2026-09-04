#!/usr/bin/env python3
"""Profile adjacent-quarter order backlog jumps without mutating source data."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from db_utils import connect_stock_db


def _context_class(excerpt: str) -> str:
    text = re.sub(r"\s+", " ", excerpt or "")
    rules = (
        ("derivative", r"파생상품|통화선도|이자율스왑|위험회피|헤지"),
        ("receivable_contract_asset", r"매출채권|계약자산|계약부채|선수금"),
        ("footnote", r"(?:주|\(\*?|\[)\s*\d{1,2}\s*\)"),
        ("opening_closing", r"기초.*기말|기말.*기초"),
        ("three_column", r"수주총액.*매출인식액.*수주잔고"),
        ("construction", r"도급|공사|분양|건설|미청구공사"),
        ("explicit_backlog", r"수주\s*잔고|수주\s*잔액|계약\s*잔고|계약\s*잔액"),
    )
    for label, pattern in rules:
        if re.search(pattern, text, re.IGNORECASE):
            return label
    return "other"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ratio", type=float, default=20.0)
    parser.add_argument("--output-dir", default=str(BASE_DIR / "research_outputs"))
    args = parser.parse_args()

    conn = connect_stock_db(timeout=60)
    conn.row_factory = __import__("sqlite3").Row
    try:
        rows = conn.execute(
            """
            SELECT stock_code, fiscal_year, fiscal_quarter, report_type,
                   backlog_amount_krw, backlog_unit, backlog_confidence,
                   source_excerpt, source_rcept_no, source_report_nm,
                   source_rcept_dt, parser_version
            FROM dart_backlog_quarterly
            WHERE backlog_amount_krw IS NOT NULL AND backlog_amount_krw > 0
            ORDER BY stock_code, report_type, fiscal_year, fiscal_quarter
            """
        ).fetchall()
    finally:
        conn.close()

    outliers = []
    previous = {}
    for row in rows:
        item = dict(row)
        key = (item["stock_code"], item["report_type"])
        prev = previous.get(key)
        if prev:
            cur_period = int(item["fiscal_year"]) * 4 + int(item["fiscal_quarter"])
            prev_period = int(prev["fiscal_year"]) * 4 + int(prev["fiscal_quarter"])
            if cur_period - prev_period == 1:
                cur_value = float(item["backlog_amount_krw"])
                prev_value = float(prev["backlog_amount_krw"])
                ratio = max(cur_value / prev_value, prev_value / cur_value)
                if ratio > args.ratio:
                    direction = "up" if cur_value > prev_value else "down"
                    outliers.append({
                        **item,
                        "previous_year": prev["fiscal_year"],
                        "previous_quarter": prev["fiscal_quarter"],
                        "previous_amount_krw": prev_value,
                        "previous_unit": prev.get("backlog_unit"),
                        "previous_confidence": prev.get("backlog_confidence"),
                        "previous_excerpt": prev.get("source_excerpt"),
                        "previous_rcept_no": prev.get("source_rcept_no"),
                        "previous_parser_version": prev.get("parser_version"),
                        "ratio": round(ratio, 3),
                        "direction": direction,
                        "context_class": _context_class(item.get("source_excerpt") or ""),
                        "previous_context_class": _context_class(prev.get("source_excerpt") or ""),
                    })
        previous[key] = item

    context_counts = Counter(row["context_class"] for row in outliers)
    unit_counts = Counter(str(row.get("backlog_unit") or "missing") for row in outliers)
    confidence_counts = Counter(str(row.get("backlog_confidence") or 0) for row in outliers)
    parser_pairs = Counter(
        f"{row.get('previous_parser_version') or 'missing'}->{row.get('parser_version') or 'missing'}"
        for row in outliers
    )
    unit_pairs = Counter(
        f"{row.get('previous_unit') or 'missing'}->{row.get('backlog_unit') or 'missing'}"
        for row in outliers
    )
    year_counts = Counter(str(row["fiscal_year"]) for row in outliers)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "ratio_threshold": args.ratio,
        "source_rows": len(rows),
        "outlier_pairs": len(outliers),
        "affected_stocks": len({row["stock_code"] for row in outliers}),
        "context_counts": dict(context_counts.most_common()),
        "unit_counts": dict(unit_counts.most_common()),
        "confidence_counts": dict(confidence_counts.most_common()),
        "parser_version_pairs": dict(parser_pairs.most_common()),
        "unit_pairs": dict(unit_pairs.most_common()),
        "year_counts": dict(sorted(year_counts.items())),
        "outliers": sorted(outliers, key=lambda row: row["ratio"], reverse=True),
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = output_dir / f"order_backlog_outlier_audit_{stamp}.json"
    latest = output_dir / "order_backlog_outlier_audit_latest.json"
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    output.write_text(encoded, encoding="utf-8")
    latest.write_text(encoded, encoding="utf-8")
    print(json.dumps({key: value for key, value in payload.items() if key != "outliers"}, ensure_ascii=False, indent=2))
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
