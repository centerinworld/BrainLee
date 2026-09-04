#!/usr/bin/env python3
"""Restore pre-v3 backlog values from an immutable backfill report."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from collectors.dart_backlog_collector import (
    _refresh_order_backlog_projection,
    _upsert_backlog_trigger,
)
from db_utils import STOCK_DB_PATH, connect_stock_db


def _restore(conn, report: dict) -> dict[str, int]:
    restored = 0
    stocks = set()
    for result in report["results"]:
        if result["action"] not in {"clear", "update", "metadata"}:
            continue
        code, year, quarter, report_type = result["key"]
        old = result["old_metric"]
        conn.execute(
            """
            UPDATE dart_backlog_quarterly SET
                backlog_amount=?,backlog_unit=?,backlog_amount_krw=?,backlog_confidence=?,
                source_excerpt=?,parser_version=?,updated_at=CURRENT_TIMESTAMP
            WHERE stock_code=? AND fiscal_year=? AND fiscal_quarter=? AND report_type=?
            """,
            (
                old.get("backlog_amount"), old.get("backlog_unit"),
                old.get("backlog_amount_krw"), old.get("backlog_confidence", 0),
                old.get("source_excerpt", ""), old.get("parser_version"),
                code, year, quarter, report_type,
            ),
        )
        amount = old.get("backlog_amount_krw")
        conn.execute(
            """
            UPDATE order_backlog SET backlog_amount=?,backlog_unit=?,backlog_normalized=?,
                collected_at=CURRENT_TIMESTAMP
            WHERE stock_code=? AND year=? AND quarter=?
            """,
            (amount, "원" if amount is not None else None, amount / 1_000_000.0 if amount is not None else None, code, year, quarter),
        )
        stocks.add((str(code), str(report_type)))
        restored += 1

    for code, report_type in stocks:
        _refresh_order_backlog_projection(conn, code, report_type)
        conn.execute(
            "DELETE FROM dart_tenbagger_triggers_quarterly WHERE stock_code=? AND metric_name='backlog'",
            (code,),
        )
        periods = conn.execute(
            """
            SELECT fiscal_year,fiscal_quarter FROM dart_backlog_quarterly
            WHERE stock_code=? AND report_type=? AND backlog_amount_krw IS NOT NULL
            """,
            (code, report_type),
        ).fetchall()
        for year, quarter in periods:
            _upsert_backlog_trigger(conn, code, int(year), int(quarter), report_type)
    return {"restored_rows": restored, "rebuilt_stocks": len(stocks)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--database", choices=("postgres", "sqlite", "both"), default="both")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    eligible = sum(r["action"] in {"clear", "update", "metadata"} for r in report["results"])
    if not args.apply:
        print(json.dumps({"dry_run": True, "restorable_rows": eligible}, ensure_ascii=False))
        return 0

    connections = []
    if args.database in {"postgres", "both"}:
        connections.append(("postgres_primary", connect_stock_db(timeout=60)))
    if args.database in {"sqlite", "both"}:
        connections.append(("sqlite_recovery", sqlite3.connect(str(STOCK_DB_PATH), timeout=60)))
    results = {}
    try:
        for name, conn in connections:
            results[name] = _restore(conn, report)
            conn.commit()
    except Exception:
        for _, conn in connections:
            conn.rollback()
        raise
    finally:
        for _, conn in connections:
            conn.close()

    output = BASE_DIR / "research_outputs" / f"order_backlog_v3_restore_{datetime.now():%Y%m%d_%H%M%S}.json"
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "databases": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
