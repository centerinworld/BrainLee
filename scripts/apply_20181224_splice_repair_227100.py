#!/usr/bin/env python3
"""Repair 227100 (프로브잇) - a splice case found outside the original 465-candidate pool.

Discovered via an independent re-scan of the 2018-12-14~2019-01-21 window looking
specifically for the 2019-01-02 boundary (see research_outputs/price_integrity_remediation_20260909/
splice_repair_candidates_0102boundary_20260911.json). 227100 was not in the original
ad-hoc candidate list at all. Same signature as the already-fixed cases: exact prior
agreement with naver_price_history_backfill for >=6 trading days, stable ~1.15x
divergence for exactly 2018-12-24~28, reconverges to ~1.0-1.02x from 2019-01-02
through 2019-01-18 (11 trading days checked).

Same safety pattern: old values backed up to price_history_fix_backup, run logged
in data_fix_log with a run_id.
"""
from __future__ import annotations

import json
import argparse
import hashlib
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402
from price_integrity import native_script, manifest_repair_status  # noqa: E402

CANDIDATES_FILE = (
    ROOT / "research_outputs" / "price_integrity_remediation_20260909"
    / "splice_repair_candidates_227100_20260911.json"
)
CANDIDATES_SHA256 = "774e83c47083c3ad742c12eaec2c65038e8b2dcbd3256ec9b44813b51d3be3bb"

BACKUP_DDL = """
CREATE TABLE IF NOT EXISTS price_history_fix_backup (
  run_id TEXT NOT NULL, stock_code TEXT NOT NULL, date TEXT NOT NULL,
  old_open DOUBLE PRECISION, old_high DOUBLE PRECISION, old_low DOUBLE PRECISION,
  old_close DOUBLE PRECISION, old_volume DOUBLE PRECISION,
  new_open DOUBLE PRECISION, new_high DOUBLE PRECISION, new_low DOUBLE PRECISION,
  new_close DOUBLE PRECISION, new_volume DOUBLE PRECISION,
  reason TEXT NOT NULL, fixed_at TEXT NOT NULL,
  PRIMARY KEY(run_id, stock_code, date)
)
"""


def run(dry_run: bool = False) -> dict:
    if hashlib.sha256(CANDIDATES_FILE.read_bytes()).hexdigest() != CANDIDATES_SHA256:
        raise RuntimeError("candidate manifest fingerprint mismatch; rebuild and review it")
    candidates = json.loads(CANDIDATES_FILE.read_text())
    run_id = f"splice_repair_naver_backfill_227100_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    conn = connect_primary_db(timeout=60)
    try:
        native_script(conn, BACKUP_DDL)
        backup_rows, update_rows, skipped = [], [], {}
        for cand in candidates:
            for d in cand["days"]:
                old = (d["ph_open"], d["ph_high"], d["ph_low"], d["ph_close"], d["ph_volume"])
                new = (d["nv_open"], d["nv_high"], d["nv_low"], d["nv_close"], d["nv_volume"])
                status = manifest_repair_status(conn, cand["stock_code"], d["date"], old, new)
                if status != 'ready':
                    skipped[status] = skipped.get(status, 0) + 1
                    continue
                backup_rows.append((
                    run_id, cand["stock_code"], d["date"],
                    d["ph_open"], d["ph_high"], d["ph_low"], d["ph_close"], d["ph_volume"],
                    d["nv_open"], d["nv_high"], d["nv_low"], d["nv_close"], d["nv_volume"],
                    "naver splice repair, independently found via 2019-01-02 boundary re-scan "
                    "(not in original 465-candidate pool), prior 6d exact match, reconverges to "
                    "ratio~1.0-1.02 through 2019-01-18",
                    datetime.now().isoformat(timespec="seconds"),
                ))
                update_rows.append((
                    d["nv_open"], d["nv_high"], d["nv_low"], d["nv_close"], d["nv_volume"],
                    cand["stock_code"], d["date"],
                ))

        result = {"run_id": run_id, "manifest_stocks": len(candidates), "ready_rows": len(update_rows),
                  "skipped_rows": skipped, "dry_run": dry_run}
        if dry_run:
            return result

        conn.execute("SELECT set_config('app.price_basis_checked','1',true)")

        conn.executemany(
            """INSERT INTO price_history_fix_backup
               (run_id,stock_code,date,old_open,old_high,old_low,old_close,old_volume,
                new_open,new_high,new_low,new_close,new_volume,reason,fixed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            backup_rows,
        )
        conn.executemany(
            """UPDATE price_history SET open=?,high=?,low=?,close=?,volume=?
               WHERE stock_code=? AND date::text=?""",
            update_rows,
        )
        conn.execute(
            """INSERT INTO data_fix_log
               (fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,
                new_value_summary,source,run_id)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                datetime.now().isoformat(timespec="seconds"), "price_history",
                "227100 (프로브잇) 2018-12-24~28 batch splice - found independently via broader "
                "2019-01-02 boundary re-scan (2018-12-14~2019-01-21 window), not present in "
                "original 465-candidate pool",
                len(update_rows),
                "UPDATE price_history SET open/high/low/close/volume = naver_price_history_backfill values",
                "price_history diverged from naver by stable ~1.15x ratio for 4 days despite exact "
                "prior agreement, reconverged to ~1.0-1.02x from 2019-01-02 onward",
                "replaced with naver_price_history_backfill OHLCV",
                "naver_price_history_backfill (Naver Finance fchart)", run_id,
            ),
        )
        conn.commit()
        return result
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(dry_run=not args.apply), ensure_ascii=False, indent=2))
