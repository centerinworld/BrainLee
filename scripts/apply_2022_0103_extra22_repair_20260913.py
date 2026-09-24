#!/usr/bin/env python3
"""Repair 22 additional 2022-01-03 splice errors found via expanded halt-disclosure veto.

While collecting more data for docs/CLAUDE_HANDOFF_TO_CODEX_20260912_price_integrity.md
items 1-2 (trading-halt/managed-stock disclosures as an additional veto source), the
"still needs scrutiny" pool from scripts/scan_recurring_splice_glitches_20260912.py
(214 episodes after the halt-disclosure cross-check) turned out to cluster heavily on
2022-01-03 (22 stocks) in addition to the 4 baseline-offset stocks already fixed in
apply_2022_baseline_offset_repair_20260913.py.

These 22 are a DIFFERENT sub-pattern from the baseline-offset 4: their price_history/
Naver ratio sits within 2% of 1.0 (parity) for a long established history (7 to 1723
prior matching days), diverges 4-27% on 2022-01-03 alone, and reconverges to within 3%
of parity over 2022-01-04~11 for every single one. Every one of the 22 also carries the
exact same created_at fingerprint already identified for 005380/005930 in
docs/CLAUDE_CHANGELOG_20260911_2022_cluster_findings.md: the neighboring dates share a
2026-04-05 09:xx:xx created_at (the main seed batch) while 2022-01-03 alone was written
by a distinct 2026-04-15 11:35:33/34 batch - the same erroneous secondary batch run
already confirmed for other stocks. No corporate_action_events row and no
price-adjusting DART disclosure (분할/병합/감자/증자/합병) falls within +/-3 days for
any of the 22.

Direct Naver OHLCV substitution is correct here (unlike the baseline-offset 4) since
these stocks' normal basis already equals Naver's 1:1.

Same safety pattern: old values backed up to price_history_fix_backup, run logged in
data_fix_log with a run_id, write-guard flag set explicitly.
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

CANDIDATES_FILE = ROOT / "research_outputs" / "price_integrity_remediation_20260909" / "jan03_extra22_confirmed_20260913.json"

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
    run_id = f"jan03_extra22_repair_20260913_{datetime.now().strftime('%H%M%S')}"
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
                f"2022-01-03 splice, 2026-04-15 batch fingerprint (prior agreement "
                f"{c['prior_matching_days']} days at parity, episode ratio "
                f"{round(c['median_ratio'], 4)}, reconverged within 3% over 2022-01-04..11)",
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
                "22 additional 2022-01-03 splice errors (2026-04-15 batch fingerprint, same "
                "root cause as 005380/005930 confirmed earlier) - found via halt-disclosure "
                "veto data collection pass, not caught by earlier recurring-splice scanner tiers",
                len(update_rows),
                "UPDATE price_history SET open/high/low/close/volume = naver value directly "
                "(these stocks' normal basis is already 1:1 with naver)",
                "price_history diverged from naver by 4-27% on 2022-01-03 alone despite long "
                "established parity, reconverged within 3% the following week",
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
