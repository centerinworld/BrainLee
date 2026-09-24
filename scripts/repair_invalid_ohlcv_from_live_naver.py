#!/usr/bin/env python3
"""Repair invalid historical OHLC rows only when live Naver confirms price basis.

The remaining invalid rows have zero open/high/low despite a positive close and
volume.  Naver's date-bounded chart is queried for each row.  A replacement is
allowed only when its close agrees with the current close within one won; this
prevents an adjusted/unadjusted basis mismatch from overwriting history.
"""

from __future__ import annotations

import argparse
from datetime import datetime

from db_compat import connect_primary_db
from price_integrity import invalid_ohlcv, native_script
from scripts.backfill_naver_ohlcv_2015_2018 import fetch


RUN_ID_PREFIX = "invalid_ohlcv_live_naver_20260920"
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
        """SELECT pja.stock_code, pja.event_date, p.open, p.high, p.low, p.close, p.volume
           FROM price_jump_audit pja
           JOIN price_history p
             ON p.stock_code=pja.stock_code AND p.date::text=pja.event_date
           WHERE pja.classification='invalid_ohlcv'
           ORDER BY pja.stock_code, pja.event_date"""
    ).fetchall()
    plan, skipped = [], {"naver_missing": 0, "basis_mismatch": 0, "naver_invalid": 0}
    for row in rows:
        day = row["event_date"]
        _, naver_rows, error = fetch(row["stock_code"], day.replace("-", ""), day.replace("-", ""))
        if error or not naver_rows:
            skipped["naver_missing"] += 1
            continue
        naver = naver_rows[0]
        new = tuple(float(value) for value in naver[2:7])
        current = tuple(float(row[key]) for key in ("open", "high", "low", "close", "volume"))
        if abs(new[3] - current[3]) > 1.0:
            skipped["basis_mismatch"] += 1
            continue
        if invalid_ohlcv(*new):
            skipped["naver_invalid"] += 1
            continue
        plan.append((row["stock_code"], day, current, new, naver[7]))
    return plan, skipped


def main(apply: bool) -> None:
    conn = connect_primary_db(timeout=180, readonly=not apply)
    try:
        if apply:
            native_script(conn, BACKUP_DDL)
        plan, skipped = build_plan(conn)
        run_id = f"{RUN_ID_PREFIX}_{datetime.now().strftime('%H%M%S')}"
        ready = []
        for code, day, old, new, source_url in plan:
            live = conn.execute(
                "SELECT open,high,low,close,volume FROM price_history WHERE stock_code=? AND date::text=?",
                (code, day),
            ).fetchone()
            if not live or tuple(float(value) for value in live) != old:
                skipped["live_row_changed"] = skipped.get("live_row_changed", 0) + 1
                continue
            ready.append((code, day, old, new, source_url))

        if apply and ready:
            now = datetime.now().isoformat(timespec="seconds")
            conn.execute("SELECT set_config('app.price_basis_checked','1',true)")
            for code, day, old, new, source_url in ready:
                conn.execute(
                    """INSERT INTO price_history_fix_backup
                       (run_id,stock_code,date,old_open,old_high,old_low,old_close,old_volume,
                        new_open,new_high,new_low,new_close,new_volume,reason,fixed_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (run_id, code, day, *old, *new,
                     "live Naver date-bounded OHLCV; close agrees within one won", now),
                )
                conn.execute(
                    """UPDATE price_history SET open=?,high=?,low=?,close=?,volume=?
                       WHERE stock_code=? AND date::text=?""",
                    (*new, code, day),
                )
            conn.execute(
                """INSERT INTO data_fix_log
                   (fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,
                    new_value_summary,source,run_id)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (now, "price_history", "remaining invalid_ohlcv rows", len(ready),
                 "replace only when live Naver close equals current close within one won",
                 "positive close/volume with zero open, high, and low",
                 "internally valid live Naver OHLCV", "Naver Finance date-bounded chart", run_id),
            )
            conn.commit()
        print({"candidates": len(plan), "ready_rows": len(ready), "skipped": skipped,
               "dry_run": not apply, "run_id": run_id})
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    main(parser.parse_args().apply)
