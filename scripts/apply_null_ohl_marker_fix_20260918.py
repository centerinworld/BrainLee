#!/usr/bin/env python3
"""Normalize NULL open/high/low on no-trade placeholder rows to the
established 0/0/0 'suspension marker' convention.

Background: price_jump_audit's 'invalid_ohlcv' classification held 1,546
rows; 1,382 of them (206 stocks, concentrated 2026-06-15~07-02 - roughly
100+ per trading day, then dropping off almost entirely) have open/high/low
= NULL while close and volume are populated (volume always 0). Cross-checked
against the Naver snapshot for all 1,382 rows: in every case Naver ALSO shows
volume=0 for these exact dates, either as the same 0/0/0/close pattern this
codebase already recognizes as a valid "no trade that day" marker
(price_integrity.invalid_ohlcv's explicit carve-out - see its docstring) or
as a flat open=high=low=close candle - both are real no-trade-day
conventions, not evidence of a wrong price.

created_at forensics on a sample (000300) show the NULL-open rows sit in the
same single batch write as neighboring rows that DID get the 0.0 marker
correctly (created_at identical) - i.e. this looks like a representation bug
in whatever backfill wrote this batch (two different code paths for "no
trade", one correct, one leaving OHL NULL), not a data corruption issue.
Root cause in the collector code was not pinned down in the time available
this session - worth a follow-up code search if this pattern recurs.

946 of the 1,382 rows already have a close value matching Naver exactly - for
these, only representation is wrong.

436 rows (48 stocks, e.g. 001470/002210/031860/etc - almost all with SOME
corporate_action_events history, though not necessarily dated exactly at
this window) show a close value that differs from Naver by a large, constant
factor across many consecutive days - the same "persistent, not isolated"
signature already used this session to identify a real adjustment-basis
difference rather than corruption (see the 2020-2021 window repair's
excluded 11+27 stocks). This script does NOT touch close/volume for any row,
for exactly this reason - only open/high/low are normalized from NULL to 0,
which is safe regardless of whether the close itself reflects a different
basis, since OHL was never populated either way.

Same safety pattern as the rest of this session: old values backed up to
price_history_fix_backup, run logged in data_fix_log with a run_id,
write-guard flag set explicitly.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402
from price_integrity import native_script  # noqa: E402

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
    conn = connect_primary_db(timeout=180)
    try:
        native_script(conn, BACKUP_DDL)

        rows = conn.execute(
            """SELECT DISTINCT pja.stock_code, pja.event_date, ph.close, ph.volume
               FROM price_jump_audit pja
               JOIN price_history ph ON ph.stock_code=pja.stock_code AND ph.date::text=pja.event_date
               WHERE pja.classification='invalid_ohlcv' AND ph.open IS NULL"""
        ).fetchall()

        run_id = f"null_ohl_marker_fix_20260918_{datetime.now().strftime('%H%M%S')}"
        backup_rows, update_rows, skipped = [], [], {}

        for stock_code, day, close, volume in rows:
            current = conn.execute(
                "SELECT open,high,low,close,volume FROM price_history WHERE stock_code=? AND substr(date,1,10)=?",
                (stock_code, day),
            ).fetchone()
            if not current or current[0] is not None:
                skipped["no_longer_null"] = skipped.get("no_longer_null", 0) + 1
                continue
            old = (None, None, None, float(current[3]) if current[3] is not None else None,
                   float(current[4]) if current[4] is not None else None)
            new_close = old[3]
            new_volume = old[4] if old[4] is not None else 0.0

            backup_rows.append((
                run_id, stock_code, day, *old, 0.0, 0.0, 0.0, new_close, new_volume,
                "NULL open/high/low on a no-trade day normalized to the established 0/0/0 "
                "suspension-marker convention (price_integrity.invalid_ohlcv carve-out); "
                "close/volume left untouched - verified against naver snapshot that volume=0 "
                "that day too, close not modified regardless of naver agreement",
                datetime.now().isoformat(timespec="seconds"),
            ))
            update_rows.append((0.0, 0.0, 0.0, new_close, new_volume, stock_code, day))

        result = {"run_id": run_id, "candidates": len(rows), "ready_rows": len(update_rows),
                   "skipped": skipped, "dry_run": dry_run}
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
                "price_jump_audit 'invalid_ohlcv' rows with NULL open/high/low on no-trade days "
                "(206 stocks, mostly 2026-06-15~07-02) - representation-only fix",
                len(update_rows),
                "UPDATE price_history SET open=0,high=0,low=0 (close/volume unchanged)",
                "open/high/low were NULL instead of the established 0/0/0 suspension-marker "
                "convention used elsewhere in this codebase for no-trade days",
                "normalized to 0/0/0 per price_integrity.invalid_ohlcv's existing convention",
                "cross-checked against naver snapshot (volume=0 confirmed for every row)", run_id,
            ),
        )
        conn.commit()
        return result
    finally:
        conn.close()


if __name__ == "__main__":
    dry = "--apply" not in sys.argv
    print(json.dumps(run(dry_run=dry), ensure_ascii=False, indent=2))
