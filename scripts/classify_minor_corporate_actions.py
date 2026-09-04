#!/usr/bin/env python3
"""Separate minor share changes from unresolved price discontinuities, with restore."""
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

BACKUP = ROOT / "research_outputs" / "minor_corporate_action_reclassification_backup.json"


def apply() -> dict:
    conn = connect_stock_db()
    try:
        rows = conn.execute(
            """SELECT id,adjustment_status,note FROM corporate_action_events
               WHERE adjustment_status='review_required' AND share_ratio BETWEEN 0.95 AND 1.05"""
        ).fetchall()
        BACKUP.write_text(json.dumps([
            {"id": int(row[0]), "adjustment_status": row[1], "note": row[2]} for row in rows
        ], ensure_ascii=False, indent=2), encoding="utf-8")
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            """UPDATE corporate_action_events
               SET adjustment_status='not_price_adjusting',
                   note='Share change is within 5%; retained for dilution review but no discontinuity factor is required.',
                   updated_at=?
               WHERE adjustment_status='review_required' AND share_ratio BETWEEN 0.95 AND 1.05""",
            (now,),
        )
        conn.commit()
        return {"mode": "apply", "updated": len(rows), "backup": str(BACKUP)}
    finally:
        conn.close()


def restore() -> dict:
    rows = json.loads(BACKUP.read_text(encoding="utf-8"))
    conn = connect_stock_db()
    try:
        conn.executemany(
            "UPDATE corporate_action_events SET adjustment_status=?,note=? WHERE id=?",
            [(row["adjustment_status"], row["note"], row["id"]) for row in rows],
        )
        conn.commit()
        return {"mode": "restore", "restored": len(rows), "backup": str(BACKUP)}
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--restore", action="store_true")
    args = parser.parse_args()
    print(json.dumps(restore() if args.restore else apply(), ensure_ascii=False, indent=2))
