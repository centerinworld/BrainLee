#!/usr/bin/env python3
"""Repair 2022-01-03 splice errors for stocks whose normal basis isn't 1.0x Naver.

docs/CLAUDE_HANDOFF_TO_CODEX_20260912_price_integrity.md item 3 flagged
000300/001080/005380/005930 as still-uncaught 2022-01-03 errors: Codex's
whole-history snapshot pipeline rejected them for unrelated reasons
(source_jump_requires_event_review / incomplete_source_coverage), and
scripts/scan_recurring_splice_glitches_20260912.py never considered them
candidates in the first place because its "matching" test required the
price_history/Naver ratio to sit within +/-2% of 1.0 - these four stocks never
traded at parity with Naver at all (their normal basis is a stable ~0.38x,
~1.03x, ~0.83x, ~0.92x respectively, presumably a real historical
split/adjustment difference between the two providers), so they could never
accumulate 5 consecutive "matching" days under that literal-1.0 definition.

Manually confirmed for these four: the price_history/Naver ratio is stable
within 0.5% across 2021-12-27..2022-01-14 (excluding 01-03 itself, and using
the ratio's own local level around 005380's 2021-12-29 step - a small,
separate, real basis shift unrelated to this repair), and on 2022-01-03
specifically it breaks from that established baseline before reverting to it
the very next trading day. No corporate_action_events or price-adjusting DART
disclosure (stock split/merger/capital reduction/rights issue) falls within
+/-3 days for any of the four - the disclosures found for 000300 that day are
routine major-shareholder/insider ownership filings, unrelated to price basis.

The correction is NOT simply "copy Naver's value" like the 1.0x-basis repairs
elsewhere this session - it's Naver's OHLCV scaled by each stock's own stable
basis ratio, since price_history's series is a differently-scaled (but
otherwise consistent) representation of the same underlying prices, not a
raw duplicate of Naver's numbers.

Same safety pattern: old values backed up to price_history_fix_backup, run
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

CANDIDATES = [
    {"stock_code": "000300", "date": "2022-01-03", "baseline_ratio": 0.38282848127591096,
     "old": [1310.0, 1320.0, 1285.0, 1295.0, 978633.0],
     "new": [8739.5914, 8805.8207, 8572.2954, 8638.9075, 56154.0]},
    {"stock_code": "001080", "date": "2022-01-03", "baseline_ratio": 1.0304441916069804,
     "old": [22700.0, 22800.0, 22400.0, 22450.0, 1165.0],
     "new": [2231.9421, 2242.2466, 2202.0592, 2207.2115, 12200.0]},
    {"stock_code": "005380", "date": "2022-01-03", "baseline_ratio": 0.8297343002392344,
     "old": [211500.0, 212500.0, 209000.0, 210500.0, 468732.0],
     "new": [175488.8045, 176318.5388, 173414.4688, 174659.0702, 468732.0]},
    {"stock_code": "005930", "date": "2022-01-03", "baseline_ratio": 0.9118824914383562,
     "old": [79400.0, 79800.0, 78200.0, 78600.0, 13502112.0],
     "new": [72403.4698, 72768.2228, 71309.2108, 71673.9638, 13502112.0]},
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
    run_id = f"baseline_offset_repair_20260913_{datetime.now().strftime('%H%M%S')}"
    conn = connect_primary_db(timeout=60)
    try:
        native_script(conn, BACKUP_DDL)
        backup_rows, update_rows, skipped = [], [], {}
        for c in CANDIDATES:
            if invalid_ohlcv(*c["new"]):
                skipped["invalid_replacement_ohlcv"] = skipped.get("invalid_replacement_ohlcv", 0) + 1
                continue
            current = conn.execute(
                "SELECT open,high,low,close,volume FROM price_history WHERE stock_code=? AND substr(date,1,10)=?",
                (c["stock_code"], c["date"]),
            ).fetchone()
            if not current:
                skipped["missing_live_row"] = skipped.get("missing_live_row", 0) + 1
                continue
            if all(abs(float(a) - float(b)) <= 1e-3 for a, b in zip(tuple(current), c["new"])):
                skipped["already_applied"] = skipped.get("already_applied", 0) + 1
                continue
            if not all(abs(float(a) - float(b)) <= 1e-3 for a, b in zip(tuple(current), c["old"])):
                skipped["live_row_changed_since_review"] = skipped.get("live_row_changed_since_review", 0) + 1
                continue
            backup_rows.append((
                run_id, c["stock_code"], c["date"], *c["old"], *c["new"],
                f"2022-01-03 splice vs Naver at stock's own established basis ratio "
                f"{round(c['baseline_ratio'], 6)} (not 1.0x) - stable within 0.5% across "
                f"2021-12-27..2022-01-14 excluding this date, no nearby corporate action/DART "
                f"price-adjusting disclosure",
                datetime.now().isoformat(timespec="seconds"),
            ))
            update_rows.append((*c["new"], c["stock_code"], c["date"]))

        result = {"run_id": run_id, "candidates": len(CANDIDATES), "ready_rows": len(update_rows),
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
                "2022-01-03 splice for 4 stocks (000300/001080/005380/005930) whose normal "
                "basis relative to Naver is not 1.0x - previously uncaught by both the recurring-"
                "splice scanner (required parity with Naver) and Codex's whole-snapshot pipeline "
                "(rejected for unrelated reasons)",
                len(update_rows),
                "UPDATE price_history SET open/high/low/close/volume = naver value * stock's own "
                "stable basis ratio (not naver value directly)",
                "price_history diverged from each stock's own established basis ratio on an "
                "isolated single day, reverted the very next trading day",
                "computed from naver full-history snapshot scaled by locally-verified basis ratio",
                "research_outputs/price_snapshot_repair/20260911T200447 naver snapshots",
                run_id,
            ),
        )
        conn.commit()
        return result
    finally:
        conn.close()


if __name__ == "__main__":
    dry = "--apply" not in sys.argv
    print(json.dumps(run(dry_run=dry), ensure_ascii=False, indent=2))
