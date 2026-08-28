#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "stock.db"
OUT = ROOT / "research_outputs" / "consensus_duplicate_cleanup_20260620.json"
BACKUP_TABLE = "consensus_targets_duplicate_backup_20260620"


DEDUP_KEY = """
COALESCE(
    CAST(report_idx AS TEXT),
    stock_code || '|' || report_date || '|' ||
    COALESCE(securities_firm, '') || '|' ||
    COALESCE(analyst, '') || '|' ||
    COALESCE(report_title, '') || '|' ||
    COALESCE(CAST(target_price AS TEXT), '')
)
"""


def scalar(conn: sqlite3.Connection, sql: str) -> int:
    return int(conn.execute(sql).fetchone()[0] or 0)


def main() -> int:
    conn = sqlite3.connect(DB, timeout=60)
    try:
        before_rows = scalar(conn, "SELECT COUNT(*) FROM consensus_targets")
        before_keys = scalar(conn, f"SELECT COUNT(DISTINCT {DEDUP_KEY}) FROM consensus_targets")
        duplicates = before_rows - before_keys

        conn.execute(f"DROP TABLE IF EXISTS {BACKUP_TABLE}")
        conn.execute(
            f"""
            CREATE TABLE {BACKUP_TABLE} AS
            WITH ranked AS (
              SELECT *,
                     ROW_NUMBER() OVER (PARTITION BY {DEDUP_KEY} ORDER BY id DESC) AS rn,
                     COUNT(*) OVER (PARTITION BY {DEDUP_KEY}) AS dup_count
              FROM consensus_targets
            )
            SELECT * FROM ranked
            WHERE dup_count > 1
            """
        )

        backup_rows = scalar(conn, f"SELECT COUNT(*) FROM {BACKUP_TABLE}")
        conn.execute(
            f"""
            DELETE FROM consensus_targets
            WHERE id IN (
              WITH ranked AS (
                SELECT id,
                       ROW_NUMBER() OVER (PARTITION BY {DEDUP_KEY} ORDER BY id DESC) AS rn
                FROM consensus_targets
              )
              SELECT id FROM ranked WHERE rn > 1
            )
            """
        )
        conn.commit()

        after_rows = scalar(conn, "SELECT COUNT(*) FROM consensus_targets")
        after_keys = scalar(conn, f"SELECT COUNT(DISTINCT {DEDUP_KEY}) FROM consensus_targets")
        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "db": str(DB),
            "backup_table": BACKUP_TABLE,
            "before_rows": before_rows,
            "before_distinct_keys": before_keys,
            "detected_duplicate_rows": duplicates,
            "backup_rows": backup_rows,
            "deleted_rows": before_rows - after_rows,
            "after_rows": after_rows,
            "after_distinct_keys": after_keys,
            "remaining_duplicate_rows": after_rows - after_keys,
        }
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
