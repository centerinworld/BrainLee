#!/usr/bin/env python3
"""Repair the 2018-12-24~28 batch-splice corruption confirmed against Naver.

Background: an "INSERT OR IGNORE"-style backfill left price_history with a
wrong price basis for a narrow window of trading days (2018-12-24 through
2018-12-28) across a large number of otherwise well-behaved stocks. Each
affected stock has YEARS of exact prior agreement with
naver_price_history_backfill (an independently-fetched, per-stock Naver
Finance series), then diverges by a stable ratio for exactly this narrow
window, with volume sometimes matching exactly - consistent with a bad batch
reload of price columns for a slice of stocks, not real market moves.

Candidate selection (see research_outputs/price_integrity_remediation_20260909/
splice_repair_candidates_20260911.json, built by ad-hoc analysis):
  - exactly one mismatched episode across the stock's entire naver-overlapping
    history (excludes stocks with a persistent/recurring basis mismatch -
    those need separate, dedicated investigation, not this repair)
  - episode <= 10 trading days
  - stable ratio within the episode (internal consistency, not noise)
  - at least 5 trading days of *exact* (within 2%) agreement immediately
    before the episode (an established track record, not a newly-listed
    stock with no comparison history)
  - not close to a "clean" corporate-action fraction (2x, 5x, 10x, 0.2x, ...)
    - those are more likely a real, not-yet-recorded stock split/consolidation
    - not this kind of data-entry/batch corruption
  - no corporate_action_events / dart_disclosures match nearby (checked
    upstream by canonical_price_history_v's classification already excluding
    confirmed/pending/nearby corporate-action dates from the candidate pool)

This script additionally restricts to episodes with median_ratio in [0.3, 3.0]
- moderate-magnitude divergence, the same order of magnitude as the
confirmed 003490/005440 cases (~1.5x). 125 more candidates with ratios outside
this range (as extreme as 76x) were deliberately excluded pending manual
review - a >10x difference is a qualitatively different kind of error that
could reflect a real unconfirmed split rather than a batch glitch, and this
script should not guess on those.

Every changed row's OLD values are preserved in price_history_fix_backup
before the UPDATE, and the run is logged in data_fix_log with a run_id.
Only OHLCV columns are touched - investor-flow columns are left untouched.

Run it, then rebuild the audit/canonical views:
  python3 scripts/apply_20181224_splice_repair.py --apply
  python3 scripts/audit_price_jumps_and_build_canonical.py
  python3 scripts/verify_price_history_with_naver.py --only-new
  python3 scripts/audit_selected_strategy_price_integrity.py
"""
from __future__ import annotations

import json
import argparse
import hashlib
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402
from price_integrity import native_script, manifest_repair_status  # noqa: E402

CANDIDATES_FILE = (
    ROOT / "research_outputs" / "price_integrity_remediation_20260909"
    / "splice_repair_candidates_20260911.json"
)
RATIO_LO, RATIO_HI = 0.3, 3.0
CANDIDATES_SHA256 = "a23475edc40eda4877a1b98866c7d95aea3c990ca50422ab6211ec6572ecbf5a"

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
    if hashlib.sha256(CANDIDATES_FILE.read_bytes()).hexdigest() != CANDIDATES_SHA256:
        raise RuntimeError("candidate manifest fingerprint mismatch; rebuild and review it")
    candidates = json.loads(CANDIDATES_FILE.read_text())
    moderate = [c for c in candidates
                if RATIO_LO <= c["median_ratio"] <= RATIO_HI
                and all('2018-12-24' <= d['date'] <= '2018-12-28' for d in c['days'])]
    run_id = f"splice_repair_naver_backfill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    conn = connect_primary_db(timeout=180)
    try:
        native_script(conn, BACKUP_DDL)

        backup_rows = []
        update_rows = []
        skipped = {}
        for cand in moderate:
            for d in cand["days"]:
                old = (d["ph_open"], d["ph_high"], d["ph_low"], d["ph_close"], d["ph_volume"])
                new = (d["nv_open"], d["nv_high"], d["nv_low"], d["nv_close"], d["nv_volume"])
                status = manifest_repair_status(conn, cand["stock_code"], d["date"], old, new)
                if status != 'ready':
                    skipped[status] = skipped.get(status, 0) + 1
                    continue
                backup_rows.append((
                    run_id, cand["stock_code"], d["date"],
                    d["ph_open"], d["ph_high"], d["ph_low"], d["ph_close"], d["ph_volume"],
                    d["nv_open"], d["nv_high"], d["nv_low"], d["nv_close"], d["nv_volume"],
                    f"naver_price_history_backfill splice repair (established prior agreement "
                    f"{cand['prior_matching_days']} days, episode ratio {round(cand['median_ratio'], 4)})",
                    datetime.now().isoformat(timespec="seconds"),
                ))
                update_rows.append((
                    d["nv_open"], d["nv_high"], d["nv_low"], d["nv_close"], d["nv_volume"],
                    cand["stock_code"], d["date"],
                ))

        result = {
            "run_id": run_id, "manifest_stocks": len(moderate), "ready_rows": len(update_rows),
            "ready_examples": [[r[5],r[6]] for r in update_rows[:20]],
            "skipped_rows": skipped, "dry_run": dry_run,
        }
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
                "2018-12-24~28 batch splice: stocks with >=5 days established prior exact "
                "agreement vs naver_price_history_backfill, single isolated 1-10 day episode, "
                f"ratio {RATIO_LO}x-{RATIO_HI}x only (more extreme-ratio candidates were left "
                "untouched pending manual review), no corporate action match",
                len(update_rows),
                "UPDATE price_history SET open/high/low/close/volume = "
                "naver_price_history_backfill values for the flagged (stock_code,date) pairs",
                "price_history diverged from naver by a stable moderate ratio on isolated dates "
                "despite years of exact prior agreement (see price_history_fix_backup for full "
                "old/new values)",
                "replaced with naver_price_history_backfill OHLCV (external_adjusted_chart_history, "
                "independently fetched per-stock from Naver)",
                "naver_price_history_backfill (Naver Finance fchart)", run_id,
            ),
        )
        conn.commit()
        return result
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(dry_run=not args.apply), ensure_ascii=False, indent=2))
