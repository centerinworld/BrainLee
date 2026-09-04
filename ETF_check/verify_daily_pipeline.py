#!/usr/bin/env python3
"""Fail the daily ETF job unless every cutover prerequisite is present."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from full_pdf_collector import DB_PATH, connect


def verify(day: str, db_path: Path = DB_PATH) -> dict:
    conn = connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        universe = conn.execute(
            "SELECT COUNT(*) FROM etf_universe_daily WHERE base_date=?", (day,)
        ).fetchone()[0]
        pdf = conn.execute(
            """
            SELECT COUNT(*) snapshots,
                   SUM(status='success') successes,
                   SUM(status='empty') empty_count,
                   SUM(status='error') error_count
            FROM etf_pdf_full_snapshot WHERE base_date=?
            """,
            (day,),
        ).fetchone()
        scale = conn.execute(
            "SELECT COUNT(*) FROM etf_scale_daily WHERE base_date=?", (day,)
        ).fetchone()[0]
        sample = conn.execute(
            """
            SELECT attempted,success,error_count,status
            FROM etfcheck_k_sample_run WHERE base_date=?
            ORDER BY run_id DESC LIMIT 1
            """,
            (day,),
        ).fetchone()
        parity = conn.execute(
            """
            SELECT passed,failures_json FROM etf_source_parity_daily
            WHERE base_date=?
            """,
            (day,),
        ).fetchone()
    finally:
        conn.close()

    failures = []
    if not universe:
        failures.append("universe_missing")
    if not pdf or int(pdf["snapshots"] or 0) != int(universe):
        failures.append("pdf_snapshot_coverage")
    if not pdf or int(pdf["successes"] or 0) != int(universe):
        failures.append("pdf_success_coverage")
    if pdf and (int(pdf["empty_count"] or 0) or int(pdf["error_count"] or 0)):
        failures.append("pdf_empty_or_error")
    if int(scale or 0) != int(universe):
        failures.append("scale_coverage")
    if not sample or int(sample["attempted"] or 0) < 60 or int(sample["success"] or 0) != int(sample["attempted"] or 0):
        failures.append("etfcheck_sample_coverage")
    if not parity or not int(parity["passed"] or 0):
        failures.append("parity_gate")

    return {
        "base_date": day,
        "ok": not failures,
        "universe": int(universe or 0),
        "pdf_successes": int(pdf["successes"] or 0) if pdf else 0,
        "scale_count": int(scale or 0),
        "sample_success": int(sample["success"] or 0) if sample else 0,
        "sample_attempted": int(sample["attempted"] or 0) if sample else 0,
        "parity_failures": json.loads(parity["failures_json"]) if parity else [],
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()
    result = verify(args.date, Path(args.db))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
