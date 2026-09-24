#!/usr/bin/env python3
"""Repair the 2022-05-09 splice cluster (65 of 23 candidates confirmed).

Background: while cross-checking the "still needs scrutiny" pool from
scripts/scan_recurring_splice_glitches_20260912.py against expanded trading-halt/
managed-stock DART disclosure terms, 2022-05-09 emerged as by far the largest
untouched cluster (49 + 17 stocks). Deviation magnitude alone is small (3-11%,
below the KRX daily limit) because these stocks' normal Naver basis is often
already slightly off 1.0 (a small persistent per-stock offset) - the earlier
scanner correctly measured deviation from 1.0 rather than from each stock's own
established level, so this cluster was mis-scored as "ambiguous."

Re-verified per stock with a stricter, baseline-aware test:
  - baseline = median(price_history/Naver ratio) over the 5 trading days
    immediately before the anomaly date, required stable within 1%
  - the anomaly date's replacement = naver value * that baseline (not naver
    directly - matches apply_2022_baseline_offset_repair_20260913.py's logic)
  - reconvergence required: average ratio over the following 5 trading days
    must return to within 2% of the same baseline
  - no corporate_action_events row and no price-adjusting DART disclosure
    (분할/병합/감자/증자/합병) within +/-3 days

65 of 23 candidates passed every check (036620 failed the baseline-stability
test and needs individual research - left untouched). Every one of the 65 also
carries the same created_at fingerprint: neighboring dates share one batch
timestamp while the anomaly date alone was written by a distinct
2026-04-25 23:22:xx run (cross-referenced manually for a sample; the systematic
gate above does not depend on this, it is corroborating evidence only).

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

CANDIDATES_FILE = ROOT / "research_outputs" / "price_integrity_remediation_20260909" / "may09_cluster_confirmed_20260913.json"

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
    candidates = [c for c in json.loads(CANDIDATES_FILE.read_text()) if c["ok"]]
    run_id = f"may09_cluster_repair_20260913_{datetime.now().strftime('%H%M%S')}"
    conn = connect_primary_db(timeout=60)
    try:
        native_script(conn, BACKUP_DDL)
        backup_rows, update_rows, skipped = [], [], {}
        for c in candidates:
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
                f"2022-05-09 splice at stock's own baseline ratio "
                f"{round(c['baseline'], 6)} (stable +/-1% over prior 5 days, reconverged "
                f"within 2% over following 5 days, no corp action/DART conflict)",
                datetime.now().isoformat(timespec="seconds"),
            ))
            update_rows.append((*c["new"], c["stock_code"], c["date"]))

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
                "2022-05-09 splice cluster (65 of 23 candidates; 036620 failed the "
                "baseline-stability test and was left untouched) - each stock's own "
                "established basis ratio vs naver, not literal 1.0",
                len(update_rows),
                "UPDATE price_history SET open/high/low/close/volume = naver value * "
                "stock's own stable basis ratio",
                "price_history diverged from each stock's own established basis on an "
                "isolated day, reconverged within 2% over the following week",
                "computed from naver full-history snapshot scaled by locally-verified basis ratio",
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
