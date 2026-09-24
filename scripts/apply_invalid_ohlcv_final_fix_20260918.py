#!/usr/bin/env python3
"""Final cleanup of price_jump_audit's 'invalid_ohlcv' bucket (163 rows after
the NULL open/high/low normalization already cleared 1,382 of the original
1,546).

Two distinct sub-patterns, verified against the Naver snapshot per row:

  1. REPLACE (66 rows): Naver's OHLCV for that date differs meaningfully from
     price_history's - most commonly open=high=low=0 in price_history despite
     a nonzero volume (real trading happened that day but no range was
     recorded), which trips invalid_ohlcv's suspension-marker carve-out
     (that carve-out only applies when volume is ALSO 0). Naver has a real,
     valid candle for these - use it.

  2. CLAMP (97 rows): Naver's OHLCV for that date matches price_history's
     current values almost exactly (within 1 won) - i.e. Naver has the SAME
     internal inconsistency (typically close exceeding high, or a flat
     open=high=low candle where close differs by a few won). Both
     independently-collected sources agree this is what actually printed
     that day, so replacing the price LEVEL with Naver's would change
     nothing and isn't the fix - the row is just not internally ordered
     (high must be >= every other field, low <= every other field). Clamp
     high=max(open,high,low,close) and low=min(open,high,low,close), leaving
     open/close/volume completely untouched. This is the minimal correction
     that satisfies price_integrity.invalid_ohlcv() without asserting a
     price value neither source actually reported.

Same safety pattern as the rest of this session: live row re-checked
immediately before writing, backed up to price_history_fix_backup, run
logged in data_fix_log with a run_id, write-guard flag set explicitly.
"""
from __future__ import annotations

import gzip
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402
from price_integrity import native_script, invalid_ohlcv  # noqa: E402

SNAP_DIR = ROOT / "research_outputs" / "price_snapshot_repair" / "20260911T200447"

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


def build_plan(conn):
    rows = conn.execute(
        """SELECT DISTINCT pja.stock_code, pja.event_date FROM price_jump_audit pja
           JOIN price_history ph ON ph.stock_code=pja.stock_code AND ph.date::text=pja.event_date
           WHERE pja.classification='invalid_ohlcv'"""
    ).fetchall()

    plan = []
    for stock_code, day in rows:
        path = SNAP_DIR / f"{stock_code}.json.gz"
        if not path.exists():
            continue
        nv = {r[1]: r for r in json.loads(gzip.decompress(path.read_bytes()))}
        nvrow = nv.get(day)
        if not nvrow:
            continue
        nv_vals = (nvrow[2], nvrow[3], nvrow[4], nvrow[5], nvrow[6])

        cur = conn.execute(
            "SELECT open,high,low,close,volume FROM price_history WHERE stock_code=? AND substr(date,1,10)=?",
            (stock_code, day),
        ).fetchone()
        if not cur or any(x is None for x in cur):
            continue
        cur = tuple(float(x) for x in cur)

        def close_enough(a, b):
            return abs(a - b) <= 1.0

        if all(close_enough(a, float(b)) for a, b in zip(cur, nv_vals)):
            hi = max(cur[0], cur[1], cur[2], cur[3])
            lo = min(cur[0], cur[1], cur[2], cur[3])
            new = (cur[0], hi, lo, cur[3], cur[4])
            reason = ("invalid_ohlcv clamp: naver matches current values within 1 won (same "
                      "source-level oddity, e.g. close printed slightly outside high) - "
                      "high/low widened to include all of open/high/low/close, open/close/"
                      "volume left untouched")
        else:
            new = tuple(float(x) for x in nv_vals)
            reason = "invalid_ohlcv replace: naver has a real, internally-consistent candle that differs from price_history"

        if invalid_ohlcv(*new):
            continue
        plan.append((stock_code, day, cur, new, reason))
    return plan


def run(dry_run: bool = False) -> dict:
    conn = connect_primary_db(timeout=120)
    try:
        native_script(conn, BACKUP_DDL)
        plan = build_plan(conn)

        run_id = f"invalid_ohlcv_final_fix_20260918_{datetime.now().strftime('%H%M%S')}"
        backup_rows, update_rows, skipped = [], [], {}

        for stock_code, day, cur, new, reason in plan:
            current = conn.execute(
                "SELECT open,high,low,close,volume FROM price_history WHERE stock_code=? AND substr(date,1,10)=?",
                (stock_code, day),
            ).fetchone()
            if not current or any(x is None for x in current):
                skipped["missing_live_row"] = skipped.get("missing_live_row", 0) + 1
                continue
            current = tuple(float(x) for x in current)
            if all(abs(a - b) <= 1e-3 for a, b in zip(current, new)):
                skipped["already_applied"] = skipped.get("already_applied", 0) + 1
                continue
            if not all(abs(a - b) <= 1e-3 for a, b in zip(current, cur)):
                skipped["live_row_changed_since_review"] = skipped.get("live_row_changed_since_review", 0) + 1
                continue

            backup_rows.append((run_id, stock_code, day, *current, *new, reason,
                                 datetime.now().isoformat(timespec="seconds")))
            update_rows.append((*new, stock_code, day))

        result = {"run_id": run_id, "candidates": len(plan), "ready_rows": len(update_rows),
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
                "price_jump_audit 'invalid_ohlcv' final cleanup, 83 stocks - two sub-patterns: "
                "naver-replace where naver has a real differing candle, internal-clamp where "
                "naver agrees with current values (source-level oddity, not a wrong value)",
                len(update_rows),
                "UPDATE price_history SET open/high/low/close/volume per per-row plan "
                "(naver substitution or high/low widening)",
                "rows failing basic OHLC sanity (e.g. close outside high/low range, or "
                "open=high=low=0 despite nonzero volume)",
                "naver substitution (66 rows) or high/low widened to internal consistency (97 rows)",
                "verified against naver full-history snapshot per row", run_id,
            ),
        )
        conn.commit()
        return result
    finally:
        conn.close()


if __name__ == "__main__":
    dry = "--apply" not in sys.argv
    print(json.dumps(run(dry_run=dry), ensure_ascii=False, indent=2))
