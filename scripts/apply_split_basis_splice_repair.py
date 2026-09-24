#!/usr/bin/env python3
"""General-purpose repair for the "future split basis applied too early" splice
bug first found and fixed for 000670(SK하이닉스) on 2026-09-23 (see hermes.md
"000670 미스터리 완전 해결"). Generalized so the same evidence-based repair can
be applied to other confirmed cases (035720/카카오 confirmed same day) without
duplicating the script per stock.

Usage per stock requires this exact evidence chain already established by hand
before running:
  1. marcap raw close/shares matches our OWN price_history for a date well
     BEFORE the suspected splice window (proves our old data was right).
  2. marcap raw close/shares stays on that SAME basis smoothly THROUGH the
     splice window with no real discontinuity (proves no real corporate
     action explains our own internal jump).
  3. DART confirms the REAL split/reduction date+ratio, which lands AFTER
     the splice window - not at its start (proves our own transition date
     doesn't correspond to any real event).
This script does not perform that verification itself - it only applies a
pre-verified marcap replacement for an explicit (stock_code, start, end)
window, exactly like scripts/apply_000670_split_basis_splice_repair_20260923.py
did as the first, hand-verified case.

--stock-code, --start, --end, --marcap-parquet (pre-fetched via marcap_client,
covering exactly [start, end] for that one stock), --reason (free text,
should name the DART evidence).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

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


def build_plan(conn, stock_code: str, marcap_parquet: str):
    marcap = pd.read_parquet(marcap_parquet)
    marcap["Date"] = marcap["Date"].astype(str)
    plan = []
    for _, row in marcap.iterrows():
        day = row["Date"]
        new_o, new_h, new_l = float(row["Open"]), float(row["High"]), float(row["Low"])
        new_c, new_v = float(row["Close"]), float(row["Volume"])
        cur = conn.execute(
            "SELECT open,high,low,close,volume FROM price_history WHERE stock_code=? AND date=?",
            (stock_code, day),
        ).fetchone()
        if not cur or any(x is None for x in cur):
            continue
        cur = tuple(float(x) for x in cur)
        new = (new_o, new_h, new_l, new_c, new_v)
        if invalid_ohlcv(new_o, new_h, new_l, new_c, new_v):
            if not (new_v == 0 and new_o == 0 and new_h == 0 and new_l == 0):
                raise RuntimeError(f"unexpected invalid replacement OHLCV for {stock_code} at {day}: {new}")
        if all(abs(a - b) <= 1.0 for a, b in zip(cur, new)):
            continue
        plan.append((day, cur, new))
    return plan


def run(stock_code: str, marcap_parquet: str, reason: str, dry_run: bool = False) -> dict:
    conn = connect_primary_db(timeout=120)
    try:
        native_script(conn, BACKUP_DDL)
        plan = build_plan(conn, stock_code, marcap_parquet)

        run_id = f"{stock_code}_split_basis_splice_repair_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        backup_rows, update_rows = [], []

        for day, cur, new in plan:
            backup_rows.append((run_id, stock_code, day, *cur, *new, reason,
                                 datetime.now().isoformat(timespec="seconds")))
            update_rows.append((*new, stock_code, day))

        result = {"run_id": run_id, "stock_code": stock_code, "candidates": len(plan),
                   "ready_rows": len(update_rows), "dry_run": dry_run}
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
            "UPDATE price_history SET open=?,high=?,low=?,close=?,volume=? WHERE stock_code=? AND date=?",
            update_rows,
        )
        conn.execute(
            """INSERT INTO data_fix_log
               (fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,
                new_value_summary,source,run_id)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                datetime.now().isoformat(timespec="seconds"), "price_history",
                f"{stock_code} split-basis splice repair",
                len(update_rows),
                "UPDATE price_history SET open/high/low/close/volume = marcap raw values",
                "OHLCV on incorrect post-split-equivalent basis, years before the real split",
                "marcap raw OHLCV for the exact affected window",
                reason, run_id,
            ),
        )
        conn.commit()
        return result
    finally:
        conn.close()


if __name__ == "__main__":
    import json
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock-code", required=True)
    parser.add_argument("--marcap-parquet", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = run(args.stock_code, args.marcap_parquet, args.reason, dry_run=not args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))
