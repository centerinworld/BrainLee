#!/usr/bin/env python3
"""Repair the largest slice of price_jump_audit's 'unresolved_active_common' pool
using Naver as ground truth, spanning 2010-2026 (not the 2020-01~2021-02 window
already handled by the two apply_2020_2021_window_corruption_repair_*.py scripts).

Background: after fully repairing the 2020-01~2021-02 window (1,048 stocks,
251,301 rows across three prior scripts this session), 'unresolved_active_common'
still held 7,167 rows spanning 2010-01-12~2026-08-21 - only 260 of which have any
stock_price_daily coverage (that raw table only spans 2020-01-02~2021-02-17), so
the stock_price_daily-based methodology used for the window fix cannot reach most
of this pool.

Grouping the 7,167 rows by their previous_date surfaced 190 dates each shared by
5+ stocks (e.g. 2014-12-30: 696 stocks, 2018-12-28: 305, 2022-05-09: 218) - the
same "one bad day poisons the next day's ratio" signature already proven twice
this session (2020-03-06, and the 2020-01-2021-02 window itself). But spot
verification against Naver showed a mixed picture: some flagged stocks on a
given cluster date matched Naver exactly (genuinely fine, coincidentally
co-flagged) while others were badly wrong - confirming that trusting the
previous_date clustering alone is not reliable enough; every row needs
individual verification.

Full methodology (uses the same pre-fetched, no-network-calls-needed Naver
snapshots at research_outputs/price_snapshot_repair/20260911T200447/{code}.json.gz
already established and cross-validated multiple times this session):
  1. For each of the 7,167 audit rows, look up BOTH the recorded previous_date
     and event_date in the stock's Naver snapshot, comparing to price_history.
     Result: 2,256 rows where only previous_date is wrong, 1,107 where only
     event_date is wrong, 3,479 where BOTH are wrong, 325 where Naver actually
     confirms BOTH dates already in price_history (genuine market move,
     mis-flagged as "unresolved" - left alone, not part of this repair).
  2. Union of all wrong (stock_code, date) pairs from the above: 9,230 unique
     pairs across 1,307 stocks.
  3. Exclude pairs whose price_history/naver ratio matches a clean split
     fraction (2x/3x/.../100x or reciprocal, +/-3%) - likely a real
     split-adjustment basis difference, not corruption: -2,504 pairs.
  4. Exclude pairs with any corporate_action_events row within +/-3 days:
     -8 pairs.
  5. Final: 6,713 rows, 1,047 stocks, 2010-2026 (heaviest in 2019: 1,650 rows,
     and 2022: 2,416 rows - both years already flagged this session as having
     chronic, repeated corruption independent of the 2020-2021 window).

Candidate list (with old/new OHLCV already resolved from the Naver snapshots)
persisted at:
  research_outputs/price_integrity_remediation_20260909/unresolved_active_common_naver_repair_20260918.json

Same safety pattern as the rest of this session: old values re-checked against
current live price_history immediately before writing (skip if changed since
the candidate list was built), backed up to price_history_fix_backup, run
logged in data_fix_log with a run_id, write-guard flag set explicitly.
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
from price_integrity import native_script, invalid_ohlcv  # noqa: E402

CANDIDATES_FILE = ROOT / "research_outputs" / "price_integrity_remediation_20260909" / "unresolved_active_common_naver_repair_20260918.json"

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
    candidates = json.loads(CANDIDATES_FILE.read_text())
    conn = connect_primary_db(timeout=300)
    try:
        native_script(conn, BACKUP_DDL)

        run_id = f"unresolved_active_common_repair_20260918_{datetime.now().strftime('%H%M%S')}"
        backup_rows, update_rows, skipped = [], [], {}

        for c in candidates:
            code, day = c["stock_code"], c["date"]
            new = tuple(float(x) for x in c["new"])
            if invalid_ohlcv(*new):
                skipped["invalid_replacement_ohlcv"] = skipped.get("invalid_replacement_ohlcv", 0) + 1
                continue
            current = conn.execute(
                "SELECT open,high,low,close,volume FROM price_history WHERE stock_code=? AND substr(date,1,10)=?",
                (code, day),
            ).fetchone()
            if not current or any(x is None for x in current):
                skipped["missing_live_row"] = skipped.get("missing_live_row", 0) + 1
                continue
            current = tuple(float(x) for x in current)
            if all(abs(a - b) <= 1e-3 for a, b in zip(current, new)):
                skipped["already_applied"] = skipped.get("already_applied", 0) + 1
                continue
            old_recorded = tuple(float(x) for x in c["old"])
            if not all(abs(a - b) <= 1e-3 for a, b in zip(current, old_recorded)):
                skipped["live_row_changed_since_review"] = skipped.get("live_row_changed_since_review", 0) + 1
                continue

            backup_rows.append((
                run_id, code, day, *current, *new,
                f"unresolved_active_common Naver ground-truth repair (ratio {round(c['ratio'], 4)}; "
                "not a clean split fraction, no corporate_action_events within +/-3 days)",
                datetime.now().isoformat(timespec="seconds"),
            ))
            update_rows.append((*new, code, day))

        result = {"run_id": run_id, "candidates": len(candidates), "ready_rows": len(update_rows),
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
                "price_jump_audit 'unresolved_active_common' pool, 2010-2026 (excludes the "
                "2020-01~2021-02 window already repaired separately): 1,047 stocks whose "
                "event_date or previous_date OHLCV disagreed with Naver by >1%, verified not a "
                "clean split-fraction ratio and no corporate action within +/-3 days",
                len(update_rows),
                "UPDATE price_history SET open/high/low/close/volume = naver full-history "
                "snapshot OHLCV for the same stock_code/date",
                "price_history diverged from Naver Finance's independently-collected daily OHLCV, "
                "not explained by a real split/corporate action",
                "replaced with naver full-history snapshot OHLCV",
                "research_outputs/price_snapshot_repair/20260911T200447 naver snapshots", run_id,
            ),
        )
        conn.commit()
        return result
    finally:
        conn.close()


if __name__ == "__main__":
    dry = "--apply" not in sys.argv
    print(json.dumps(run(dry_run=dry), ensure_ascii=False, indent=2))
