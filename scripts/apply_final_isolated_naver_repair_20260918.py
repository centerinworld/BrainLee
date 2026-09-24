#!/usr/bin/env python3
"""Final small batch from the comprehensive full-history Naver scan: 295 rows,
12 stocks, confirmed genuinely isolated (not a legitimate adjustment-basis
difference).

Background: scripts/full_naver_comparison_scan_20260918.py compared price_history
against Naver for ALL 4,751 KR common stocks' full history (not just previously
flagged jump dates) at a >15% deviation threshold, surfacing 1,330,438
mismatches. The overwhelming majority turned out to be noise from legitimate,
unrecorded split/adjustment-basis differences, not corruption:
  - 995 of 1,431 involved stocks had SOME corporate_action_events row (excluded).
  - Of the remaining 436, 419 were still CHRONIC (>50 mismatch days despite no
    recorded corporate action) - i.e. corporate_action_events itself is
    incomplete, and these are very likely real, unrecorded adjustment-basis
    differences persisting across the stock's whole history, not a bug.
  - Of the 17 remaining "non-chronic" stocks, 5 failed a snap-back check
    (price_history did not converge back to Naver in the 5 trading days after
    their flagged run ended) - also excluded as likely unrecorded adjustments.
  - 12 stocks (295 rows) passed every filter: isolated run, no recorded
    corporate action, AND snaps back to matching Naver within 5% immediately
    after the run - the same signature validated repeatedly this session for
    genuine isolated corruption.

This is intentionally a small, conservative batch. The much larger remaining
pattern (~1,300 stocks system-wide with chronic Naver/price_history
divergence) is a different, bigger problem - it looks like a genuine gap in
this project's split/rights-issue adjustment-basis bookkeeping
(corporate_action_events under-coverage), not a data corruption bug, and
fixing it properly would mean building out that adjustment system, not
substituting Naver values wholesale (which could just as easily replace a
correct nominal price with a differently-adjusted one). Not attempted here -
see the handoff doc for a full writeup.

Candidate list (old/new OHLCV already resolved) at:
  research_outputs/price_integrity_remediation_20260909/final_isolated_naver_repair_20260918.json

Same safety pattern as the rest of this session: live row re-checked against
the recorded 'old' value before writing, backed up to
price_history_fix_backup, run logged in data_fix_log with a run_id,
write-guard flag set explicitly.
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

CANDIDATES_FILE = ROOT / "research_outputs" / "price_integrity_remediation_20260909" / "final_isolated_naver_repair_20260918.json"

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
    conn = connect_primary_db(timeout=60)
    try:
        native_script(conn, BACKUP_DDL)

        run_id = f"final_isolated_naver_repair_20260918_{datetime.now().strftime('%H%M%S')}"
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
                "full-history naver scan, final isolated batch: confirmed no corp-action "
                "history, isolated run, snaps back to naver within 5% right after",
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
                "final isolated batch from full-history naver scan: 12 stocks, confirmed "
                "not a legitimate adjustment-basis difference (no corp action history, "
                "snaps back to naver within 5% right after the flagged run)",
                len(update_rows),
                "UPDATE price_history SET open/high/low/close/volume = naver full-history "
                "snapshot OHLCV for the same stock_code/date",
                "price_history diverged from Naver by >15%, isolated (not chronic), "
                "reconverged immediately after",
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
