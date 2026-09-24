#!/usr/bin/env python3
"""Repair a 2020-01-02~2021-02-17 batch-corruption window across 112 stocks.

Background: apply_externally_confirmed_corruption_repair_20260917.py fixed 481
isolated boundary-day rows flagged by price_jump_audit's day-over-day jump
detector. Rebuilding the canonical views right after that fix showed the very
next trading day for one of those stocks (152550) was STILL wrong - because a
run of consecutive days that are ALL off by the same multiplier looks
"normal" to a single-day ratio check (each day's ratio vs its neighbor stays
~1.0 even though every value in the run is wrong).

Widening the comparison against stock_price_daily (independently-collected
raw KRX OHLCV, which happens to have dense daily coverage for exactly this
window) across the full 2020-01-02~2021-02-17 span for the 237 stocks in the
original corruption family surfaced 62,957 mismatching stock-days - but many
of those are NOT corruption: 114 stocks show a clean split-fraction ratio
(2x/3x/5x/10x/20x/50x etc.) or have a real corporate_action_events row nearby,
consistent with price_history being correctly split-adjusted while
stock_price_daily is not (or vice versa) - those must not be touched here.

Diagnostic used to separate real corruption from an adjustment-basis
difference: does the price_history/stock_price_daily ratio SNAP BACK to
~1.0 immediately after 2021-02-17 (isolated one-time batch bug - a real
back-adjustment factor would not just stop applying at an arbitrary
historical date), or does it PERSIST at a similar level afterward (an
ongoing adjustment-convention difference, not this bug)?
  - 112 of 123 "unexplained" stocks snap back to within 5% of 1.0 in their
    first ~5 stock_price_daily observations after 2021-02-17 -> genuine,
    isolated corruption confined to the window. THESE are repaired here.
  - 11 stocks persist at ~0.90-0.95 well past the window (016450, 072870,
    001750, 001755, 002960, 003475, 035000, 037710, 110790, 039570, 053210)
    -> excluded, need separate investigation, not part of this bug.

Same safety pattern as the rest of this session: old values backed up to
price_history_fix_backup, run logged in data_fix_log with a run_id,
write-guard flag set explicitly. Only rows within the confirmed window
that actually differ from stock_price_daily by >1% are touched - rows
already matching (e.g. already fixed by the 20260917 run) are skipped.
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

FIX_TARGET_CODES = [
    '000145','000225','000227','000670','000700','000815','000860','000970','001120','001270',
    '001275','001460','001465','001720','001795','002200','002420','002460','003200','003470',
    '003540','003545','003547','003570','003620','003690','003920','003925','004970','005257',
    '005385','005387','005389','005800','005830','005940','005945','006060','006125','006740',
    '006980','007330','007340','007590','008500','009270','009680','010100','011785','012700',
    '015360','016360','016610','017390','017670','017940','023150','023450','025530','029780',
    '030000','030200','031430','033920','034830','034950','035510','036530','038390','040420',
    '058850','060980','064960','071055','071090','075130','078930','078935','084010','084870',
    '086280','086790','088260','089600','092230','094800','100250','100840','108675','110990',
    '112610','120115','138930','139130','149980','152550','175330','181710','183190','192400',
    '204210','214320','214370','225190','285130','293580','316140','334890','338100','348950',
    '357120','357250',
]

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

        placeholders = ",".join(["?"] * len(FIX_TARGET_CODES))
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
            tuple(FIX_TARGET_CODES) + (WINDOW_START, WINDOW_END),
        ).fetchall()

        run_id = f"window_2020_2021_corruption_repair_20260918_{datetime.now().strftime('%H%M%S')}"
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
                "2020-01~2021-02 batch corruption window: stock_price_daily (independent raw KRX) "
                "vs price_history mismatch confirmed as isolated (ratio snaps back to ~1.0 "
                "immediately after 2021-02-17, no clean split-fraction pattern, no nearby "
                "corporate_action_events)",
                datetime.now().isoformat(timespec="seconds"),
            ))
            update_rows.append((*new, stock_code, day))

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
                f"2020-01-02~2021-02-17 batch corruption window across {len(FIX_TARGET_CODES)} stocks "
                "(subset of the 237-stock family found while re-auditing the 20260917 fix; excludes "
                "114 stocks explained by clean split-fraction ratios or nearby real corporate actions, "
                "and 11 stocks whose ratio persists past the window boundary)",
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
