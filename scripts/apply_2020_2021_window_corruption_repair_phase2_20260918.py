#!/usr/bin/env python3
"""Phase 2 of the 2020-01-02~2021-02-17 batch-corruption repair: 811 more stocks.

Background: apply_2020_2021_window_corruption_repair_20260918.py (phase 1) fixed
112 stocks (29,998 rows) in this window, found via the 237-stock family that
originated from the narrower 1,090-row externally_confirmed_internal_corruption
pool. Re-checking price_jump_audit afterward showed the mismatch rate against
stock_price_daily (independent raw KRX OHLCV) held steady at ~62-65% of ALL
~2,265-2,315 comparable stocks on every sampled day throughout the ENTIRE
window that table covers (2020-01-02~2021-02-17; the table has zero coverage
before or after that range) - meaning the 237-stock family was only a small,
pre-filtered slice of a much bigger problem.

Applying the exact same validated methodology across every stock with any
stock_price_daily coverage in the window (2,416 stocks, 634,365 comparable
rows), not just the original 237-stock family:
  1. Exclude stocks whose median ratio is already within 3% of 1.0 (no issue).
  2. Exclude stocks whose median ratio matches a clean split fraction
     (2x/3x/4x/5x/6x/8x/10x/20x/25x/50x/100x or their reciprocals, +/-3%) -
     price_history is very likely correctly split-adjusted for a real later
     corporate action while stock_price_daily is not (or vice versa).
  3. Exclude stocks with any corporate_action_events row in/near the window
     (2019-06-01~2021-09-30) - same reasoning.
  4. Of the remainder (849 stocks), exclude 27 whose ratio PERSISTS at a
     similar level in stock_price_daily's first ~5 observations after
     2021-02-17 (an ongoing adjustment-convention difference, not this
     bug) and 11 with no stock_price_daily coverage after the window at all
     (cannot verify - includes 310840, already established elsewhere this
     session as an unrelated SPAC/suspended-marker scanner false positive).
  5. The remaining 811 stocks all snap back to within 5% of 1.0 immediately
     after the window ends - the same isolated-batch-bug signature already
     validated for phase 1's 112 stocks. NONE of these 811 overlap with the
     237 stocks already fixed (confirmed: 0 overlap, because those are now
     genuinely fixed and no longer show up as "suspicious" against current
     price_history).

Full target list persisted at:
  research_outputs/price_integrity_remediation_20260909/window_2020_2021_phase2_targets_20260918.json

Estimated scope: 811 stocks, ~220,822 rows (>1% mismatch vs stock_price_daily
within the window) - about 7x the size of phase 1.

Same safety pattern as the rest of this session: old values backed up to
price_history_fix_backup, run logged in data_fix_log with a run_id,
write-guard flag set explicitly. Only rows that actually differ from
stock_price_daily by >1% are touched.
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

WINDOW_START = "2020-01-02"
WINDOW_END = "2021-02-17"

TARGETS_FILE = ROOT / "research_outputs" / "price_integrity_remediation_20260909" / "window_2020_2021_phase2_targets_20260918.json"

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
    fix_target_codes = json.loads(TARGETS_FILE.read_text())
    conn = connect_primary_db(timeout=300)
    try:
        native_script(conn, BACKUP_DDL)

        placeholders = ",".join(["?"] * len(fix_target_codes))
        candidates = conn.execute(
            f"""SELECT ph.stock_code, ph.date::text AS d,
                       ph.open, ph.high, ph.low, ph.close, ph.volume,
                       spd.open_price, spd.high_price, spd.low_price, spd.close_price, spd.volume
                FROM price_history ph
                JOIN stock_price_daily spd
                  ON spd.stock_code = ph.stock_code AND spd.bas_dt = REPLACE(ph.date::text,'-','')
                WHERE ph.stock_code IN ({placeholders})
                  AND ph.date::text BETWEEN ? AND ?
                  AND spd.close_price > 0 AND ph.close > 0
                  AND ABS(ph.close - spd.close_price) / spd.close_price > 0.01
                ORDER BY ph.stock_code, ph.date""",
            tuple(fix_target_codes) + (WINDOW_START, WINDOW_END),
        ).fetchall()

        run_id = f"window_2020_2021_phase2_repair_20260918_{datetime.now().strftime('%H%M%S')}"
        backup_rows, update_rows, skipped = [], [], {}

        for row in candidates:
            stock_code, day = row[0], row[1]
            current = tuple(float(x) for x in row[2:7])
            new = tuple(float(x) for x in row[7:12])
            if invalid_ohlcv(*new):
                skipped["invalid_replacement_ohlcv"] = skipped.get("invalid_replacement_ohlcv", 0) + 1
                continue
            if all(abs(a - b) <= 1e-3 for a, b in zip(current, new)):
                skipped["already_applied"] = skipped.get("already_applied", 0) + 1
                continue

            backup_rows.append((
                run_id, stock_code, day, *current, *new,
                "2020-01~2021-02 batch corruption window, phase 2 (811-stock full-universe sweep): "
                "stock_price_daily (independent raw KRX) vs price_history mismatch confirmed as "
                "isolated (ratio snaps back to ~1.0 immediately after 2021-02-17, no clean "
                "split-fraction pattern, no nearby corporate_action_events)",
                datetime.now().isoformat(timespec="seconds"),
            ))
            update_rows.append((*new, stock_code, day))

        result = {"run_id": run_id, "target_stocks": len(fix_target_codes),
                   "candidates": len(candidates), "ready_rows": len(update_rows),
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
                f"2020-01-02~2021-02-17 batch corruption window, phase 2: {len(fix_target_codes)} "
                "additional stocks beyond phase 1's 237-stock family (full-universe sweep against "
                "stock_price_daily; excludes stocks explained by clean split-fraction ratios, "
                "nearby real corporate actions, or a ratio that persists past the window boundary)",
                len(update_rows),
                "UPDATE price_history SET open/high/low/close/volume = stock_price_daily "
                "(independently-collected raw KRX OHLCV) for the same stock_code/date, only where "
                "the pre-fix ratio snapped back to ~1.0 immediately after 2021-02-17",
                "price_history diverged from stock_price_daily by >1% throughout the window and "
                "reconverged to parity right after the window ended - isolated batch bug signature, "
                "not an ongoing split-adjustment basis difference",
                "replaced with stock_price_daily raw OHLCV (matches independent raw KRX source)",
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
