#!/usr/bin/env python3
"""Verify the v3 backlog backfill in PostgreSQL and recovery SQLite.

Optionally rebuild every backlog trigger from quality-eligible source rows.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from db_utils import STOCK_DB_PATH, connect_stock_db
from collectors.dart_backlog_collector import _refresh_order_backlog_projection


MIN_CONFIDENCE = 0.95
MAX_ADJACENT_RATIO = 20.0


def _comparable(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return True
    if left <= 0 or right <= 0:
        return False
    return max(left, right) / min(left, right) <= MAX_ADJACENT_RATIO


def _rebuild_triggers(conn) -> dict[str, int]:
    projection = {"accepted": 0, "rejected": 0}
    stock_reports = conn.execute(
        "SELECT DISTINCT stock_code,report_type FROM dart_backlog_quarterly"
    ).fetchall()
    for stock_code, report_type in stock_reports:
        counts = _refresh_order_backlog_projection(conn, str(stock_code), str(report_type))
        projection["accepted"] += counts["accepted"]
        projection["rejected"] += counts["rejected"]

    rows = conn.execute(
        """
        SELECT stock_code,fiscal_year,fiscal_quarter,report_type,
               backlog_amount_krw,backlog_confidence
        FROM dart_backlog_quarterly
        WHERE backlog_amount_krw IS NOT NULL
        ORDER BY stock_code,report_type,fiscal_year,fiscal_quarter
        """
    ).fetchall()
    grouped = defaultdict(dict)
    for row in rows:
        grouped[(str(row[0]), str(row[3]))][(int(row[1]), int(row[2]))] = (
            float(row[4]), float(row[5] or 0)
        )

    projection_rejected_keys = set()
    for (stock_code, _), periods in grouped.items():
        for period, (amount, confidence) in periods.items():
            if confidence < MIN_CONFIDENCE or amount <= 0:
                projection_rejected_keys.add((stock_code, *period))
        ordered = sorted(periods)
        for previous, current in zip(ordered, ordered[1:]):
            expected = (previous[0], previous[1] + 1) if previous[1] < 4 else (previous[0] + 1, 1)
            if current != expected:
                continue
            left, left_confidence = periods[previous]
            right, right_confidence = periods[current]
            if left_confidence < MIN_CONFIDENCE or right_confidence < MIN_CONFIDENCE:
                continue
            if not _comparable(left, right):
                projection_rejected_keys.add((stock_code, *previous))
                projection_rejected_keys.add((stock_code, *current))

    conn.execute("DELETE FROM dart_tenbagger_triggers_quarterly WHERE metric_name='backlog'")
    inserted = 0
    rejected_low_confidence = 0
    rejected_discontinuity = 0
    trigger_rejected_keys = set()
    for (stock_code, report_type), periods in grouped.items():
        for (year, quarter), (current, confidence) in periods.items():
            if confidence < MIN_CONFIDENCE or current <= 0:
                rejected_low_confidence += 1
                trigger_rejected_keys.add((stock_code, year, quarter, report_type))
                continue
            previous_key = (year, quarter - 1) if quarter > 1 else (year - 1, 4)
            next_key = (year, quarter + 1) if quarter < 4 else (year + 1, 1)
            year_ago_key = (year - 1, quarter)
            previous = periods.get(previous_key)
            next_row = periods.get(next_key)
            year_ago = periods.get(year_ago_key)
            qv = previous[0] if previous and previous[1] >= MIN_CONFIDENCE else None
            nv = next_row[0] if next_row and next_row[1] >= MIN_CONFIDENCE else None
            yv = year_ago[0] if year_ago and year_ago[1] >= MIN_CONFIDENCE else None
            if not _comparable(current, qv) or not _comparable(current, nv) or not _comparable(current, yv):
                rejected_discontinuity += 1
                trigger_rejected_keys.add((stock_code, year, quarter, report_type))
                continue
            qoq = ((current - qv) / abs(qv) * 100.0) if qv else None
            yoy = ((current - yv) / abs(yv) * 100.0) if yv else None
            level = "BACKLOG_SURGE" if yoy is not None and yoy >= 25.0 else None
            conn.execute(
                """
                INSERT INTO dart_tenbagger_triggers_quarterly(
                    stock_code,fiscal_year,fiscal_quarter,report_type,
                    metric_name,metric_value,yoy_pct,qoq_pct,trigger_level,
                    source_table,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(stock_code,fiscal_year,fiscal_quarter,report_type,metric_name)
                DO UPDATE SET metric_value=excluded.metric_value,yoy_pct=excluded.yoy_pct,
                    qoq_pct=excluded.qoq_pct,trigger_level=excluded.trigger_level,
                    source_table=excluded.source_table,updated_at=CURRENT_TIMESTAMP
                """,
                (
                    stock_code, year, quarter, report_type, "backlog", current,
                    yoy, qoq, level, "dart_backlog_quarterly",
                ),
            )
            inserted += 1

    projected_keys = {
        (str(r[0]), int(r[1]), int(r[2]))
        for r in conn.execute(
            "SELECT stock_code,year,quarter FROM order_backlog WHERE backlog_amount IS NOT NULL"
        ).fetchall()
    }
    trigger_keys = {
        (str(r[0]), int(r[1]), int(r[2]), str(r[3]))
        for r in conn.execute(
            """SELECT stock_code,fiscal_year,fiscal_quarter,report_type
               FROM dart_tenbagger_triggers_quarterly WHERE metric_name='backlog'"""
        ).fetchall()
    }
    return {
        "source_rows": len(rows),
        "projection_accepted": projection["accepted"],
        "projection_rejected": projection["rejected"],
        "inserted": inserted,
        "rejected_low_confidence": rejected_low_confidence,
        "rejected_discontinuity": rejected_discontinuity,
        "projection_quality_leaks": len(projected_keys & projection_rejected_keys),
        "trigger_quality_leaks": len(trigger_keys & trigger_rejected_keys),
    }


def _verify_report(conn, report: dict) -> dict[str, int]:
    checked = 0
    mismatches = 0
    missing = 0
    for result in report["results"]:
        if result["action"] not in {"clear", "update", "metadata"}:
            continue
        code, year, quarter, report_type = result["key"]
        row = conn.execute(
            """
            SELECT backlog_amount_krw,backlog_confidence,parser_version
            FROM dart_backlog_quarterly
            WHERE stock_code=? AND fiscal_year=? AND fiscal_quarter=? AND report_type=?
            """,
            (code, year, quarter, report_type),
        ).fetchone()
        checked += 1
        if row is None:
            missing += 1
            continue
        expected = None if result["action"] == "clear" else result["new_metric"]["backlog_amount_krw"]
        actual = row[0]
        same_value = actual is None if expected is None else (
            actual is not None and abs(float(actual) - float(expected)) <= max(abs(float(expected)), 1.0) * 1e-9
        )
        if not same_value or str(row[2]) != str(report["parser_version"]):
            mismatches += 1
    return {"checked": checked, "missing": missing, "mismatches": mismatches}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--rebuild-triggers", action="store_true")
    args = parser.parse_args()
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))

    connections = [("postgres_primary", connect_stock_db(timeout=60))]
    from config import IS_POSTGRES
    if IS_POSTGRES:
        connections.append(("sqlite_recovery", sqlite3.connect(str(STOCK_DB_PATH), timeout=60)))

    results = {}
    try:
        for name, conn in connections:
            verification = _verify_report(conn, report)
            trigger_rebuild = _rebuild_triggers(conn) if args.rebuild_triggers else None
            conn.commit()
            results[name] = {"verification": verification, "trigger_rebuild": trigger_rebuild}
    finally:
        for _, conn in connections:
            conn.close()

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_report": str(Path(args.report).resolve()),
        "policy": {
            "minimum_confidence": MIN_CONFIDENCE,
            "maximum_adjacent_ratio": MAX_ADJACENT_RATIO,
        },
        "databases": results,
    }
    output = BASE_DIR / "research_outputs" / "order_backlog_v3_cutover_verification.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    failed = any(
        value["verification"]["mismatches"]
        or value["verification"]["missing"]
        or (value["trigger_rebuild"] or {}).get("projection_quality_leaks", 0)
        or (value["trigger_rebuild"] or {}).get("trigger_quality_leaks", 0)
        for value in results.values()
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
