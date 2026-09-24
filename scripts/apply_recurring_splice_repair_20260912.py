#!/usr/bin/env python3
"""Repair recurring, scattered splice/stale-write glitches found across full history.

Background: docs/CLAUDE_CHANGELOG_20260911_2022_cluster_findings.md identified the
"2022 cluster" via a day-level threshold (>=8 stocks breaching +-30% simultaneously).
A full per-stock scan against Codex's complete Naver snapshots
(research_outputs/price_snapshot_repair/20260911T200447/{code}.json.gz) found the
true scope is much larger and not limited to 2022 or to simultaneous multi-stock
days - individual stocks show isolated 1-2 day episodes scattered from 2018 through
2026 (scripts/scan_recurring_splice_glitches_20260912.py, 471 raw candidate
episodes across 274 stocks).

The original manifest selected 91 episodes whose cross-provider ratio was
outside a KRX daily price band and 155 episodes whose stored close repeated the
prior close. A 2026-09-12 independent review found neither test is conclusive:
the KRX band applies to one series' day-over-day move, not a price_history/Naver
basis ratio, and a repeated close can be valid during suspension or no trading.
The manifest is retained for reproducibility and rollback, not treated as a
fresh blanket authorization.

Candidates still require the upstream shape checks: >=5 consecutive
Naver-matching (within 2%) trading days immediately before the episode, episode
<=10 days, stable ratio through the episode, not a clean corporate-action
fraction, and reconvergence to Naver within 5% over the following 5 trading
days. This runner now independently enforces corporate-action and relevant DART
notice exclusion within +/-3 calendar days, pins the manifest hash, and
revalidates both live and staged source rows before any write.

The remaining 225 "needs scrutiny" episodes (small deviation, no stale-write
signature) are deliberately NOT included - they cannot yet be distinguished from
ordinary cross-provider rounding/timing noise without per-stock research, exactly
the same caution already applied to 003490/005440-style extreme ratios in
apply_20181224_splice_repair.py.

Same safety pattern throughout this session: old values backed up to
price_history_fix_backup, run logged in data_fix_log with a run_id, only OHLCV
columns touched, write-guard flag set explicitly (not bypassed silently).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402
from price_integrity import native_script, manifest_repair_status  # noqa: E402
from scripts.scan_recurring_splice_glitches_20260912 import (  # noqa: E402
    PRICE_ADJUSTING_REPORT_TERMS,
    overlaps_corporate_action_window,
)

CANDIDATES_FILE = (
    ROOT / "research_outputs" / "price_integrity_remediation_20260909"
    / "recurring_splice_confirmed_20260912.json"
)
CANDIDATES_SHA256 = "3ab690165fdae5b4ba228f797c1b47ccea2ac6b53e76aae92303a48699df696e"

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
    run_id = f"recurring_splice_repair_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    conn = connect_primary_db(timeout=180)
    try:
        native_script(conn, BACKUP_DDL)

        event_dates: dict[str, set[str]] = defaultdict(set)
        for code, event_date in conn.execute(
            "SELECT stock_code,event_date::text FROM corporate_action_events"
        ).fetchall():
            event_dates[code].add(event_date)
        for code, raw_date, report_name in conn.execute(
            "SELECT stock_code,CAST(rcept_dt AS TEXT),report_nm FROM dart_disclosures"
        ).fetchall():
            if not any(term in str(report_name or "") for term in PRICE_ADJUSTING_REPORT_TERMS):
                continue
            compact = str(raw_date or "").replace("-", "")[:8]
            if len(compact) == 8 and compact.isdigit():
                event_dates[code].add(f"{compact[:4]}-{compact[4:6]}-{compact[6:]}")

        backup_rows, update_rows, skipped = [], [], {}
        for cand in candidates:
            if overlaps_corporate_action_window(
                cand["run_dates"], event_dates.get(cand["stock_code"], set())
            ):
                skipped["corporate_action_or_disclosure_within_3_days"] = (
                    skipped.get("corporate_action_or_disclosure_within_3_days", 0)
                    + len(cand["days"])
                )
                continue
            for d in cand["days"]:
                old = (d["ph_open"], d["ph_high"], d["ph_low"], d["ph_close"], d["ph_volume"])
                new = (d["nv_open"], d["nv_high"], d["nv_low"], d["nv_close"], d["nv_volume"])
                status = manifest_repair_status(conn, cand["stock_code"], d["date"], old, new)
                if status != "ready":
                    skipped[status] = skipped.get(status, 0) + 1
                    continue
                backup_rows.append((
                    run_id, cand["stock_code"], d["date"], *old, *new,
                    f"recurring scattered splice/stale-write repair (prior agreement "
                    f"{cand['prior_matching_days']} days, episode ratio {round(cand['median_ratio'], 4)})",
                    datetime.now().isoformat(timespec="seconds"),
                ))
                update_rows.append((*new, cand["stock_code"], d["date"]))

        result = {
            "run_id": run_id, "manifest_stocks": len(set(c["stock_code"] for c in candidates)),
            "manifest_episodes": len(candidates), "ready_rows": len(update_rows),
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
                "Recurring scattered splice/stale-write glitches (2018-2026, not limited to the "
                "2018-12-24 or 2022 clusters) - two confirmed tiers: (a) ratio vs naver breaches "
                "the actual KRX daily price-limit band, (b) smaller deviation but the flagged "
                "day's close is bit-identical to the prior day's close (stale carry-forward). "
                "225 smaller-deviation episodes without the stale-write signature were left "
                "untouched pending per-stock research.",
                len(update_rows),
                "UPDATE price_history SET open/high/low/close/volume = naver full-history "
                "snapshot values (research_outputs/price_snapshot_repair/20260911T200447)",
                "price_history diverged from naver on an isolated 1-10 day episode despite years "
                "of established prior agreement and reconverged within 5 trading days after",
                "replaced with naver full-history snapshot OHLCV (Naver Finance fchart, complete "
                "per-stock history already fetched by scripts/backfill_naver_ohlcv_2015_2018.py's "
                "fetch() during the 2026-09-11 price_snapshot_repair dry-run)",
                "price_snapshot_repair/20260911T200447 naver snapshots", run_id,
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
