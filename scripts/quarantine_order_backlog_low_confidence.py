#!/usr/bin/env python3
"""Quarantine legacy heuristic backlog values while retaining their evidence."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from collectors.dart_backlog_collector import PARSER_VERSION
from db_utils import STOCK_DB_PATH, connect_stock_db


SELECT_SQL = """
SELECT stock_code,fiscal_year,fiscal_quarter,report_type,
       backlog_amount,backlog_unit,backlog_amount_krw,backlog_confidence,
       source_excerpt,parser_version
FROM dart_backlog_quarterly
WHERE backlog_amount_krw IS NOT NULL AND backlog_confidence <= 0.6
ORDER BY stock_code,fiscal_year,fiscal_quarter,report_type
"""


def _load(conn) -> list[dict]:
    results = []
    for row in conn.execute(SELECT_SQL).fetchall():
        old = {
            "backlog_amount": row[4], "backlog_unit": row[5],
            "backlog_amount_krw": row[6], "backlog_confidence": row[7],
            "source_excerpt": row[8], "parser_version": row[9],
        }
        results.append({
            "key": [str(row[0]), int(row[1]), int(row[2]), str(row[3])],
            "old_metric": old,
            "new_metric": {
                "backlog_amount": None, "backlog_unit": None,
                "backlog_amount_krw": None, "backlog_confidence": 0,
                "source_excerpt": row[8],
            },
            "action": "clear",
            "reason": "legacy_generic_low_confidence_quarantine",
        })
    return results


def _apply(conn, results: list[dict]) -> None:
    for result in results:
        code, year, quarter, report_type = result["key"]
        conn.execute(
            """
            UPDATE dart_backlog_quarterly SET backlog_amount=NULL,backlog_unit=NULL,
                backlog_amount_krw=NULL,backlog_confidence=0,parser_version=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE stock_code=? AND fiscal_year=? AND fiscal_quarter=? AND report_type=?
            """,
            (PARSER_VERSION, code, year, quarter, report_type),
        )
        conn.execute(
            """
            UPDATE order_backlog SET backlog_amount=NULL,backlog_unit=NULL,
                backlog_normalized=NULL,backlog_to_rev=NULL,collected_at=CURRENT_TIMESTAMP
            WHERE stock_code=? AND year=? AND quarter=?
            """,
            (code, year, quarter),
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    primary = connect_stock_db(timeout=60)
    results = _load(primary)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "parser_version": PARSER_VERSION,
        "apply": args.apply,
        "targets": len(results),
        "actions": {"clear": len(results)},
        "results": results,
    }
    output = BASE_DIR / "research_outputs" / f"order_backlog_low_confidence_quarantine_{datetime.now():%Y%m%d_%H%M%S}.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.apply:
        legacy = sqlite3.connect(str(STOCK_DB_PATH), timeout=60)
        try:
            _apply(primary, results)
            _apply(legacy, results)
            primary.commit()
            legacy.commit()
        except Exception:
            primary.rollback()
            legacy.rollback()
            raise
        finally:
            legacy.close()
    primary.close()
    print(json.dumps({"apply": args.apply, "targets": len(results), "output": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
