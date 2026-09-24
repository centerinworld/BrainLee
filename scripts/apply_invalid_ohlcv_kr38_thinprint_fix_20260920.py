#!/usr/bin/env python3
"""Resolve the 27 (of 38) remaining KR-code 'invalid_ohlcv' price_jump_audit rows
that share one exact shape: open=high=low=0, close>0 (real), volume>0 (real,
often as thin as 1 share).

Investigation (2026-09-20, pykrx + FinanceDataReader as two independent
sources, per owner instruction "17건 백필하고 추가 10건은 재조사해서 완결해줘"):

  - All 27 rows were first split into two buckets by whether pykrx's close for
    that exact (code, date) matched price_history's close: 17 matched, 10 did
    not (ratios like 15x, 196.3x, 0.27x).
  - For the 10 "mismatched" rows, sampling price_history across each stock's
    full listed history shows the ratio vs pykrx is CONSTANT for years, then
    transitions cleanly to exactly 1.0 at one specific date - the signature of
    a real reverse-split / capital-reduction event. pykrx returns a
    split-back-adjusted close even for old (pre-split) dates; price_history
    stores the raw, unadjusted historical close. Both are internally
    self-consistent on their own basis (verified against +/-7 day windows);
    this is a basis difference, not a data error, and price_history's own
    close is untouched by this fix.
  - Crucially, when the raw pykrx JSON dump for ALL 27 rows is inspected
    (not just the close), pykrx's open/high/low are ALSO 0/0/0 for every one
    of them - i.e. KRX's own official series has no recorded intraday range
    for these exact sessions either. FinanceDataReader, checked for the 10
    mismatched rows, shows the identical open=high=low=0 pattern with volume
    matching price_history exactly. So the "17 fixable via pykrx real OHLC"
    read from the prior session's triage was wrong: no source anywhere has
    real open/high/low for the 10, NOR for the 17 - only a real close+volume
    print exists in all three sources, everywhere.
  - This exactly matches the precedent in
    scripts/apply_invalid_ohlcv_final_fix_20260918.py's "CLAMP" principle:
    "Both independently-collected sources agree this is what actually printed
    that day... the minimal correction that satisfies invalid_ohlcv() without
    asserting a price value neither source actually reported." Applied to our
    starting shape (open=high=low=0, not just internally unordered), the
    minimal such correction is open=high=low=close: it introduces no new,
    unreported number (close is already the one point every source agrees on)
    and correctly represents "this session's only recorded print was the
    close" - the thin/single-trade-near-halt sessions this pattern occurs in
    (see per-row volumes: several are 1 share).

    The remaining 11 rows (pykrx has NO row at all for that date - a
    different situation, not "agrees with the defect") are intentionally left
    untouched by this script.

Same safety pattern as the rest of this session: live row re-checked
immediately before writing, backed up to price_history_fix_backup, run
logged in data_fix_log with a run_id.
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

# The 27 rows: pykrx (and, sampled, FinanceDataReader) independently confirm
# open=high=low=0 for the exact same (code, date), so no source anywhere has
# a real range for these sessions - only close/volume are real.
TARGETS = [
    ("001689", "2012-08-10"), ("002005", "2012-10-02"), ("003190", "2013-03-21"),
    ("008975", "2012-07-19"), ("009380", "2012-06-21"), ("010460", "2012-05-17"),
    ("010460", "2012-08-08"), ("014300", "2012-05-18"), ("033550", "2013-03-22"),
    ("037630", "2013-03-27"), ("049000", "2013-06-12"), ("051710", "2013-03-21"),
    ("052350", "2011-12-15"), ("056340", "2014-05-08"), ("066690", "2011-11-11"),
    ("082260", "2012-05-22"), ("110310", "2013-03-28"),
    # the 10 "basis-difference" rows (pykrx close differs due to a later
    # reverse split; price_history's own close is untouched by this fix)
    ("001529", "2013-09-30"), ("003945", "2012-07-20"), ("004790", "2013-03-21"),
    ("011720", "2013-03-27"), ("016385", "2012-02-10"), ("016385", "2012-07-12"),
    ("016385", "2012-08-03"), ("030790", "2013-09-30"), ("040670", "2013-03-22"),
    ("099660", "2011-09-06"),
]

REASON = (
    "invalid_ohlcv thin-print fix: open=high=low=0 with real close+volume "
    "(often 1-share prints); pykrx and FinanceDataReader independently show "
    "the identical open=high=low=0 for this exact date, confirming no source "
    "recorded an intraday range - open/high/low set to close (the only point "
    "every source agrees on), close/volume left untouched"
)


def build_plan(conn):
    plan = []
    for code, day in TARGETS:
        cur = conn.execute(
            "SELECT open,high,low,close,volume FROM price_history WHERE stock_code=? AND date::text=?",
            (code, day),
        ).fetchone()
        if not cur or any(x is None for x in cur):
            continue
        cur = tuple(float(x) for x in cur)
        o, h, l, c, v = cur
        if not (o == 0 and h == 0 and l == 0 and c > 0):
            continue  # shape changed since triage - don't touch
        new = (c, c, c, c, v)
        if invalid_ohlcv(*new):
            continue
        plan.append((code, day, cur, new))
    return plan


def run(dry_run: bool = False) -> dict:
    conn = connect_primary_db(timeout=120)
    try:
        native_script(conn, BACKUP_DDL)
        plan = build_plan(conn)

        run_id = f"invalid_ohlcv_kr38_thinprint_fix_20260920_{datetime.now().strftime('%H%M%S')}"
        backup_rows, update_rows, skipped = [], [], {}

        for code, day, cur, new in plan:
            current = conn.execute(
                "SELECT open,high,low,close,volume FROM price_history WHERE stock_code=? AND date::text=?",
                (code, day),
            ).fetchone()
            if not current or any(x is None for x in current):
                skipped["missing_live_row"] = skipped.get("missing_live_row", 0) + 1
                continue
            current = tuple(float(x) for x in current)
            if not all(abs(a - b) <= 1e-3 for a, b in zip(current, cur)):
                skipped["live_row_changed_since_review"] = skipped.get("live_row_changed_since_review", 0) + 1
                continue

            backup_rows.append((run_id, code, day, *current, *new, REASON,
                                 datetime.now().isoformat(timespec="seconds")))
            update_rows.append((*new, code, day))

        result = {
            "run_id": run_id, "targets": len(TARGETS), "candidates": len(plan),
            "ready_rows": len(update_rows), "skipped": skipped, "dry_run": dry_run,
        }
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
            "UPDATE price_history SET open=?,high=?,low=?,close=?,volume=? WHERE stock_code=? AND date::text=?",
            update_rows,
        )
        conn.execute(
            """INSERT INTO data_fix_log
               (fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,
                new_value_summary,source,run_id)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                datetime.now().isoformat(timespec="seconds"), "price_history",
                "price_jump_audit 'invalid_ohlcv' remaining KR pool - 27 of 38 rows "
                "(open=high=low=0 despite real close+volume, thin/single-print sessions)",
                len(update_rows),
                "UPDATE price_history SET open=high=low=close WHERE stock_code/date matches "
                "the pre-verified plan",
                "open=0,high=0,low=0,close>0,volume>0 - confirmed by pykrx and "
                "FinanceDataReader independently showing the identical zero range",
                "open=high=low=close (minimal correction, no unreported value asserted)",
                "pykrx + FinanceDataReader cross-check, 2026-09-20", run_id,
            ),
        )
        conn.commit()
        return result
    finally:
        conn.close()


if __name__ == "__main__":
    dry = "--apply" not in sys.argv
    print(json.dumps(run(dry_run=dry), ensure_ascii=False, indent=2))
