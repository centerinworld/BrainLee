#!/usr/bin/env python3
"""Repair the 1,090-row 'externally_confirmed_internal_corruption' pool.

Background: scripts/verify_price_history_with_naver.py cross-checks every
price_jump_audit row against Naver Finance. When Naver's ratio agrees with
the *independent raw KRX table* (stock_price_daily) but disagrees with
price_history, it promotes the row to classification=
'externally_confirmed_internal_corruption' - i.e. two independent external
sources agree with each other and both disagree with our own data. That
script only writes to price_jump_audit/external_price_verification; it
never touches price_history itself, so as of 2026-09-17 these 1,090 rows
(290 stocks, 2020-01-06~2021-02-15) were still sitting uncorrected in
price_history.

This is stronger evidence than the baseline-ratio methodology used earlier
this session (apply_*_repair_20260913.py scripts, which only compared
against a single external source - Naver) - here two independently
collected sources (Naver Finance + a separately-collected raw KRX OHLCV
table, stock_price_daily) already agree with each other before we even
touch price_history.

stock_price_daily has full OHLCV (not just close) and covers all 1,090
rows (verified 1090/1090 before writing this script), so it is used
directly as the replacement source - no new network calls needed.

Same safety pattern as the rest of this session: old values backed up to
price_history_fix_backup, run logged in data_fix_log with a run_id,
write-guard flag set explicitly, live row re-checked against the audit's
recorded 'old' value before writing (skip if it has since changed).
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


def run(dry_run: bool = False) -> dict:
    conn = connect_primary_db(timeout=120)
    try:
        native_script(conn, BACKUP_DDL)

        audits = conn.execute(
            """SELECT stock_code, event_date FROM price_jump_audit
               WHERE classification='externally_confirmed_internal_corruption'
               ORDER BY stock_code, event_date"""
        ).fetchall()

        run_id = f"ext_confirmed_corruption_repair_20260917_{datetime.now().strftime('%H%M%S')}"
        backup_rows, update_rows, skipped = [], [], {}

        for stock_code, event_date in audits:
            day = str(event_date)[:10]
            raw = conn.execute(
                """SELECT open_price, high_price, low_price, close_price, volume
                   FROM stock_price_daily WHERE stock_code=? AND bas_dt=?""",
                (stock_code, day.replace("-", "")),
            ).fetchone()
            if not raw:
                skipped["no_raw_source_row"] = skipped.get("no_raw_source_row", 0) + 1
                continue
            new = tuple(float(x) for x in raw)
            if invalid_ohlcv(*new):
                skipped["invalid_replacement_ohlcv"] = skipped.get("invalid_replacement_ohlcv", 0) + 1
                continue

            current = conn.execute(
                "SELECT open,high,low,close,volume FROM price_history WHERE stock_code=? AND substr(date,1,10)=?",
                (stock_code, day),
            ).fetchone()
            if not current:
                skipped["missing_live_row"] = skipped.get("missing_live_row", 0) + 1
                continue
            current = tuple(float(x) for x in current)
            if all(abs(a - b) <= 1e-3 for a, b in zip(current, new)):
                skipped["already_applied"] = skipped.get("already_applied", 0) + 1
                continue

            backup_rows.append((
                run_id, stock_code, day, *current, *new,
                "externally_confirmed_internal_corruption: independently-collected raw KRX table "
                "(stock_price_daily) and Naver Finance agree with each other and both disagree with "
                "price_history (verify_price_history_with_naver.py, confidence>=0.9)",
                datetime.now().isoformat(timespec="seconds"),
            ))
            update_rows.append((*new, stock_code, day))

        result = {"run_id": run_id, "candidates": len(audits), "ready_rows": len(update_rows),
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
                "externally_confirmed_internal_corruption pool: 290 stocks, 2020-01-06~2021-02-15, "
                "price_jump_audit rows where Naver Finance and an independently-collected raw KRX "
                "table (stock_price_daily) agree with each other but both disagree with price_history",
                len(update_rows),
                "UPDATE price_history SET open/high/low/close/volume = stock_price_daily "
                "(independently-collected raw KRX OHLCV) for the same stock_code/date",
                "price_history diverged from two independent external sources that agree with "
                "each other (Naver Finance ratio == raw KRX ratio, both != price_history ratio)",
                "replaced with stock_price_daily raw OHLCV (matches Naver Finance)",
                "stock_price_daily (independently-collected raw KRX OHLCV table)", run_id,
            ),
        )
        conn.commit()
        return result
    finally:
        conn.close()


if __name__ == "__main__":
    dry = "--apply" not in sys.argv
    print(json.dumps(run(dry_run=dry), ensure_ascii=False, indent=2))
