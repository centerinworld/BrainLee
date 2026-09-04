#!/usr/bin/env python3
"""Fail selected backtest runs whose holding windows cross unusable prices."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_utils import connect_stock_db  # noqa: E402
from run_registry import register_artifact  # noqa: E402

AUDIT = ROOT / "research_outputs" / "selected_strategy_price_integrity_latest.json"
BACKUP = ROOT / "research_outputs" / "price_integrity_artifact_backup_20260814.json"


def apply() -> dict:
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    failed = {
        row["strategy"]: row
        for row in audit.get("strategies", [])
        if row.get("status") == "failed"
    }
    conn = connect_stock_db(readonly=True)
    try:
        suites = conn.execute(
            """SELECT strategy,run_hash FROM selected_run_registry
               WHERE report_type='strategy_center'"""
        ).fetchall()
        targets = []
        for strategy, suite_hash in suites:
            if strategy not in failed:
                continue
            members = conn.execute(
                "SELECT run_hash FROM backtest_run_set_members WHERE suite_hash=?",
                (suite_hash,),
            ).fetchall()
            targets.append((strategy, suite_hash, [row[0] for row in members]))
        existing = conn.execute(
            """SELECT run_hash,artifact_type,passed,details_json,artifact_hash,created_at
               FROM run_verification_artifacts
               WHERE artifact_type='price_integrity'"""
        ).fetchall()
    finally:
        conn.close()

    BACKUP.write_text(json.dumps({
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "existing": [list(row) for row in existing],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    registered = 0
    for strategy, suite_hash, members in targets:
        evidence = failed[strategy]
        details = {
            "strategy": strategy,
            "suite_hash": suite_hash,
            "holding_windows": evidence.get("holding_windows"),
            "contaminated_windows": evidence.get("contaminated_windows"),
            "audit_path": str(AUDIT),
            "checked_at": audit.get("checked_at"),
        }
        for run_hash in members:
            register_artifact(run_hash, "price_integrity", False, details)
            registered += 1
    return {"failed_strategies": len(targets), "artifacts_registered": registered, "backup": str(BACKUP)}


def restore() -> dict:
    payload = json.loads(BACKUP.read_text(encoding="utf-8"))
    conn = connect_stock_db()
    try:
        conn.execute("DELETE FROM run_verification_artifacts WHERE artifact_type='price_integrity'")
        rows = payload.get("existing") or []
        if rows:
            conn.executemany(
                """INSERT INTO run_verification_artifacts
                   (run_hash,artifact_type,passed,details_json,artifact_hash,created_at)
                   VALUES(?,?,?,?,?,?)""",
                rows,
            )
        conn.commit()
    finally:
        conn.close()
    return {"restored": len(payload.get("existing") or [])}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--restore", action="store_true")
    args = parser.parse_args()
    print(json.dumps(restore() if args.restore else apply(), ensure_ascii=False, indent=2))
