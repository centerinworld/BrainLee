#!/usr/bin/env python3
"""Remove the stale-duplicate price_history rows fabricated for 2026-07-17.

Two independent sources agree 2026-07-17 (a Friday) had no trading at all:
  - The official KRX approved API (data-dbg.krx.co.kr sto/stk_bydd_trd and
    sto/ksq_bydd_trd) returns an empty OutBlock_1 for basDd=20260717, while
    the neighboring trading days (07-16, 07-20) return ~940 rows each.
  - Naver's per-stock chart API (siseJson.naver) skips 2026-07-17 entirely for
    005930 (Samsung Electronics, one of the most liquid tickers on the
    exchange) - its daily series goes 07-16 -> 07-20 with no 07-17 row.

Despite that, price_history holds 191 rows dated 2026-07-17 (created_at around
06:38-06:45 that morning, consistent with an early intraday collector run
before whatever caused the closure was known). Every one of those 191 rows is
bit-for-bit identical (close AND volume) to that same stock's 2026-07-16 row -
a collector carried the prior day's values forward instead of recognizing the
market was closed and skipping the date, not a genuine (if quiet) trading
session. This is the root cause of the 2,500-row coverage_gap classification
on 2026-07-20 (docs/CLAUDE_HANDOFF_TO_CODEX_20260912_price_integrity.md item 1):
canonical_price_history_v's LAG-based previous-date lookup treats these 191
stale rows as if trading happened on 07-17, so those 191 stocks compare 07-20
against a phantom same-value 07-17 "session" while the other ~2,500 stocks
correctly skip straight from 07-16 - an internally inconsistent previous-date
reference across the same trading day.

Deleting the 191 rows (not the trigger's concern - it only fires on
INSERT/UPDATE of OHLCV columns, not DELETE) makes 07-17 uniformly absent for
every stock, so canonical_price_history_v's coverage_gap classification for
07-20 becomes consistent and, more importantly, correct: the actual last
trading day before 07-20 is 07-16 for every stock, not a fabricated 07-17.

Every deleted row is preserved in price_history_fix_backup first (reusing this
session's existing table/columns; old_* holds the deleted row, new_* is NULL
to signal "removed, not replaced").
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

TARGET_DATE = "2026-07-17"
COMPARE_DATE = "2026-07-16"

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
    run_id = f"remove_stale_20260717_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    conn = connect_primary_db(timeout=60)
    try:
        native_script(conn, BACKUP_DDL)
        # Scoped to 6-character KR equity codes only. The other 20 rows present
        # on 2026-07-17 are global indices/FX/futures (^VIX, ^IXIC, JPYKRW=X,
        # GC=F, ...) that legitimately move independently of the Korean market
        # calendar - deleting or comparing those is out of scope here.
        rows = conn.execute(
            """
            SELECT a.stock_code, a.open, a.high, a.low, a.close, a.volume
            FROM price_history a JOIN price_history b
              ON a.stock_code = b.stock_code
            WHERE a.date::text = ? AND b.date::text = ?
              AND a.stock_code ~ '^[0-9A-Z]{6}$'
              AND a.close = b.close AND a.volume = b.volume
            """,
            (TARGET_DATE, COMPARE_DATE),
        ).fetchall()
        # Confirm nothing genuinely differs before deleting anything - if this
        # count doesn't match the full 07-17 KR-equity row count, something
        # changed since this script was written and it should stop rather than guess.
        total = conn.execute(
            "SELECT COUNT(*) FROM price_history WHERE date::text = ? AND stock_code ~ '^[0-9A-Z]{6}$'",
            (TARGET_DATE,),
        ).fetchone()[0]
        result = {"run_id": run_id, "date": TARGET_DATE, "stale_duplicate_rows": len(rows),
                  "total_rows_on_date": total, "dry_run": dry_run}
        if len(rows) != total:
            result["aborted"] = "row count changed since this script was reviewed; not deleting anything"
            return result
        if dry_run or not rows:
            return result

        backup_rows = [
            (run_id, code, TARGET_DATE, o, h, l, c, v, None, None, None, None, None,
             "2026-07-17 stale carry-forward duplicate of 2026-07-16 (both sources "
             "confirm the market was closed - KRX approved API and Naver both have "
             "zero rows for this date); removed to stop the phantom trading-day "
             "reference that caused the 2026-07-20 coverage_gap spillover",
             datetime.now().isoformat(timespec="seconds"))
            for code, o, h, l, c, v in rows
        ]
        conn.executemany(
            """INSERT INTO price_history_fix_backup
               (run_id,stock_code,date,old_open,old_high,old_low,old_close,old_volume,
                new_open,new_high,new_low,new_close,new_volume,reason,fixed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            backup_rows,
        )
        conn.execute(
            "DELETE FROM price_history WHERE date::text = ? AND stock_code ~ '^[0-9A-Z]{6}$'",
            (TARGET_DATE,),
        )
        conn.execute(
            """INSERT INTO data_fix_log
               (fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,
                new_value_summary,source,run_id)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                datetime.now().isoformat(timespec="seconds"), "price_history",
                "2026-07-17 stale carry-forward duplicates - all 191 rows on this date were "
                "bit-identical to 2026-07-16 despite KRX approved API and Naver both showing "
                "zero data for this date (market closed)",
                len(rows),
                "DELETE FROM price_history WHERE date='2026-07-17' (all rows confirmed "
                "identical to the prior trading day, not real observations)",
                "191 rows dated 2026-07-17, all matching 2026-07-16's close/volume exactly",
                "removed (no replacement - the date has no genuine trading data)",
                "cross-checked against KRX data-dbg.krx.co.kr and Naver siseJson.naver "
                "(both empty for this date)",
                run_id,
            ),
        )
        conn.commit()
        result["deleted"] = len(rows)
        return result
    finally:
        conn.close()


if __name__ == "__main__":
    dry = "--apply" not in sys.argv
    print(json.dumps(run(dry_run=dry), ensure_ascii=False, indent=2))
