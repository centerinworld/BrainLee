#!/usr/bin/env python3
"""Repair the 22 extreme-ratio 2018-12-24~28 splice candidates that reconverge.

Follow-up to apply_20181224_splice_repair.py (which handled the 340
moderate-ratio [0.3x,3.0x] candidates). Of the 125 candidates outside that
range, this script targets only the subset independently confirmed safe:
naver_price_history_backfill was extended through 2019-02-28
(scripts/extend_naver_backfill_20181101_20190228.py) and each of these 22
stocks was checked to have close/naver ratio within [0.9, 1.1] over the first
~10 trading days after 2019-01-02 - i.e. price_history realigns with naver
almost exactly once the corrupted window ends, the same signature as the
already-confirmed 003490/005440 cases, just with a larger in-episode error
magnitude (up to ~10.6x instead of ~1.5x).

The remaining 103 candidates were checked and explicitly rejected for
different reasons - e.g. 001230 shows a persistent ~4.4x-5.3x divergence from
naver both during AND after the episode (inconsistent with either a clean
splice-then-recovery or a matching corporate action), and 009730 shows
naver itself jumping ~10x starting 2019-01-02 while price_history continues
its pre-episode trend (consistent with an unconfirmed real corporate action,
not a price_history-side data bug). Those need dedicated per-stock research,
not a blanket rule, and are intentionally left untouched here.

Same safety pattern as the moderate-ratio repair: every changed row's OLD
values are preserved in price_history_fix_backup, the run is logged in
data_fix_log with a run_id, and only OHLCV columns are touched.
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
    / "splice_repair_candidates_extreme_reconverged_20260911.json"
)
CANDIDATES_SHA256 = "275d34fcb329f9169de00792afec129cd3e32786162640f21307c0f43363880f"

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
    run_id = f"splice_repair_naver_backfill_extreme_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    conn = connect_primary_db(timeout=180)
    try:
        native_script(conn, BACKUP_DDL)

        backup_rows = []
        update_rows = []
        skipped = {}
        for cand in candidates:
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
                    f"naver_price_history_backfill splice repair, extreme-ratio bucket "
                    f"(established prior agreement {cand['prior_matching_days']} days, "
                    f"episode ratio {round(cand['median_ratio'], 4)}, confirmed reconverged to "
                    f"naver within +/-10% over first 10 trading days after 2019-01-02)",
                    datetime.now().isoformat(timespec="seconds"),
                ))
                update_rows.append((
                    d["nv_open"], d["nv_high"], d["nv_low"], d["nv_close"], d["nv_volume"],
                    cand["stock_code"], d["date"],
                ))

        result = {
            "run_id": run_id, "manifest_stocks": len(candidates), "ready_rows": len(update_rows),
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
                "2018-12-24~28 batch splice, extreme-ratio bucket: 22 of 125 candidates outside "
                "the 0.3x-3.0x range independently confirmed by re-verifying price/naver "
                "reconvergence over 2019-01-02~02-15 (ratio within +/-10%); the other 103 were "
                "left untouched (persistent divergence or naver-side jump, not a clean splice signature)",
                len(update_rows),
                "UPDATE price_history SET open/high/low/close/volume = "
                "naver_price_history_backfill values for the flagged (stock_code,date) pairs",
                "price_history diverged from naver by an extreme ratio on isolated dates despite "
                "years of exact prior agreement AND reconverged to naver within days after "
                "(see price_history_fix_backup for full old/new values)",
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
