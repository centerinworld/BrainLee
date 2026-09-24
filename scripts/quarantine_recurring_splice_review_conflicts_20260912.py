#!/usr/bin/env python3
"""Quarantine recurring-splice repairs whose automatic proof was invalid.

The original confirmed manifest did not actually enforce its documented
corporate-action +/-3-day veto and did not inspect nearby DART price-adjusting
notices. This script leaves price_history and its exact repair backup untouched,
but prevents affected rows from entering canonical returns until reviewed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402
from scripts.scan_recurring_splice_glitches_20260912 import (  # noqa: E402
    PRICE_ADJUSTING_REPORT_TERMS,
    overlaps_corporate_action_window,
)

MANIFEST = (ROOT / "research_outputs" / "price_integrity_remediation_20260909"
            / "recurring_splice_confirmed_20260912.json")
MANIFEST_SHA256 = "3ab690165fdae5b4ba228f797c1b47ccea2ac6b53e76aae92303a48699df696e"
REPAIR_RUN_ID = "recurring_splice_repair_20260912_110400"
REASON = "recurring_splice_auto_confirmation_invalidated"


def _same(left, right) -> bool:
    return len(left) == len(right) and all(
        a is not None and b is not None and abs(float(a) - float(b)) <= 1e-6
        for a, b in zip(left, right)
    )


def run(*, apply: bool = False) -> dict:
    if hashlib.sha256(MANIFEST.read_bytes()).hexdigest() != MANIFEST_SHA256:
        raise RuntimeError("confirmed manifest fingerprint mismatch")
    manifest = json.loads(MANIFEST.read_text())
    conn = connect_primary_db(timeout=180)
    try:
        event_dates: dict[str, set[str]] = defaultdict(set)
        evidence_by_code: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for code, event_date, event_type, status in conn.execute(
            "SELECT stock_code,event_date::text,event_type,adjustment_status FROM corporate_action_events"
        ).fetchall():
            event_dates[code].add(event_date)
            evidence_by_code[code].append((event_date, f"corporate_action:{event_date}:{event_type}:{status}"))
        for code, raw_date, report_name in conn.execute(
            "SELECT stock_code,CAST(rcept_dt AS TEXT),report_nm FROM dart_disclosures"
        ).fetchall():
            if not any(term in str(report_name or "") for term in PRICE_ADJUSTING_REPORT_TERMS):
                continue
            compact = str(raw_date or "").replace("-", "")[:8]
            if len(compact) == 8 and compact.isdigit():
                event_date = f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"
                event_dates[code].add(event_date)
                evidence_by_code[code].append((event_date, f"dart:{event_date}:{str(report_name).strip()}"))

        ready = []
        skipped: dict[str, int] = defaultdict(int)
        for episode in manifest:
            code = episode["stock_code"]
            if not overlaps_corporate_action_window(episode["run_dates"], event_dates.get(code, set())):
                continue
            lower = date.fromisoformat(min(episode["run_dates"])) - timedelta(days=3)
            upper = date.fromisoformat(max(episode["run_dates"])) + timedelta(days=3)
            relevant = [
                text for event_date, text in evidence_by_code[code]
                if lower <= date.fromisoformat(event_date) <= upper
            ]
            for day in episode["days"]:
                backup = conn.execute(
                    """SELECT new_open,new_high,new_low,new_close,new_volume
                       FROM price_history_fix_backup
                       WHERE run_id=? AND stock_code=? AND date=?""",
                    (REPAIR_RUN_ID, code, day["date"]),
                ).fetchone()
                current = conn.execute(
                    """SELECT open,high,low,close,volume FROM price_history
                       WHERE stock_code=? AND substr(date,1,10)=?""",
                    (code, day["date"]),
                ).fetchone()
                expected = (day["nv_open"], day["nv_high"], day["nv_low"], day["nv_close"], day["nv_volume"])
                if not backup:
                    skipped["not_applied_by_reviewed_run"] += 1
                elif not current or not _same(tuple(current), tuple(backup)) or not _same(tuple(current), expected):
                    skipped["live_or_backup_value_changed"] += 1
                else:
                    ready.append((code, day["date"], "; ".join(relevant[:8])))

        result = {"reason": REASON, "ready_rows": len(ready), "skipped": dict(skipped), "dry_run": not apply}
        if not apply:
            return result
        created_at = datetime.now().isoformat(timespec="seconds")
        conn.executemany(
            """INSERT INTO price_integrity_quarantine(stock_code,event_date,reason,evidence,created_at)
               VALUES(?,?,?,?,?) ON CONFLICT(stock_code,event_date,reason)
               DO UPDATE SET evidence=excluded.evidence,created_at=excluded.created_at""",
            [(code, day, REASON,
              "Original recurring-splice auto-confirmation violated its documented corporate-action/DART +/-3-day veto. " + evidence,
              created_at) for code, day, evidence in ready],
        )
        conn.commit()
        result["inserted_or_updated"] = len(ready)
        return result
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(apply=args.apply), ensure_ascii=False, indent=2))
