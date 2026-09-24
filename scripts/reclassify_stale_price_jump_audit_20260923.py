#!/usr/bin/env python3
"""Re-classify price_jump_audit rows left stale by the bulk batch-interpolation
repair (scripts/apply_bulk_marcap_interpolation_repair_20260923.py, 2.18M rows
fixed across 2019-2026).

price_jump_audit is a physical table, snapshotted by the last full run of
scripts/audit_price_jumps_and_build_canonical.py - which predates the bulk
price fix. Its stored previous_close/event_close for the fixed (stock_code,
date) pairs no longer match price_history, so canonical_price_history_v marks
them 'stale_audit' but - per that view's own logic - still forces
return_usable=0 for any row with a matching audit entry at all, stale or not.
A full re-run of the audit script rebuilds the *entire* table (DELETE + full
re-scan of price_history_quality_v across the whole market) and is known to
time out on refresh_calendar's full-table GROUP BY scan even before this
session's changes (see hermes.md). Since only a small, precisely known subset
of rows are actually stale (identified below by a direct value comparison
against price_history, not a full rebuild), this script instead:

  1. Finds every price_jump_audit row whose stored previous_close or
     event_close no longer matches the (now-corrected) price_history value.
  2. Re-reads that (stock_code, event_date) from price_history_quality_v
     (a live view - already reflects the price fix).
  3. If the live quality_status is now normal/insufficient_history, the
     "jump" was purely an artifact of the bad data - the audit row is
     deleted outright (nothing to classify anymore).
  4. Otherwise, re-runs the EXACT same classification logic as
     audit_price_jumps_and_build_canonical.py's run() loop (copied verbatim,
     not reimplemented) against the corrected values, and UPDATEs the row
     in place with the new classification/return_usable/evidence.

This intentionally duplicates that loop body rather than importing it, because
the original is not factored into a reusable per-row function and this is a
one-off targeted cleanup (same pattern as apply_split_basis_splice_repair.py
generalizing apply_000670_..._repair.py) - not a second permanent owner of
price_jump_audit's classification rules.

--apply to actually write (default dry-run reports counts only).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402
from price_integrity import outside_band  # noqa: E402


def find_stale(conn):
    rows = conn.execute(
        """
        SELECT a.stock_code, a.event_date FROM price_jump_audit a
        JOIN price_history ph ON ph.stock_code=a.stock_code AND ph.date=a.event_date
        JOIN price_history phprev ON phprev.stock_code=a.stock_code AND phprev.date=a.previous_date
        WHERE ph.close != a.event_close OR phprev.close != a.previous_close
        """
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def classify_one(conn, code: str, event_date: str, current_common: set):
    row = conn.execute(
        """SELECT stock_code,substr(date,1,10) event_date,close,prev_close previous_close,
                  substr(previous_date,1,10) previous_date,quality_status
           FROM price_history_quality_v WHERE stock_code=? AND substr(date,1,10)=?""",
        (code, event_date),
    ).fetchone()
    if row is None:
        return ("delete", None)
    quality_status = row[5]
    if quality_status in ("normal", "insufficient_history", "suspended"):
        return ("delete", None)

    close, previous_close, previous_date = row[2], row[3], row[4]
    ratio = (float(close) / float(previous_close)
             if close is not None and previous_close and previous_close > 0 else None)

    prev_raw = conn.execute(
        "SELECT close_price FROM stock_price_daily WHERE stock_code=? AND bas_dt=? ORDER BY bas_dt DESC LIMIT 1",
        (code, (previous_date or "").replace("-", "")),
    ).fetchone()
    cur_raw = conn.execute(
        "SELECT close_price FROM stock_price_daily WHERE stock_code=? AND bas_dt=?",
        (code, event_date.replace("-", "")),
    ).fetchone()
    raw_prev = float(prev_raw[0]) if prev_raw and prev_raw[0] else None
    raw_cur = float(cur_raw[0]) if cur_raw and cur_raw[0] else None
    raw_ratio = raw_cur / raw_prev if raw_prev and raw_cur else None

    d = datetime.strptime(event_date, "%Y-%m-%d")
    lo, hi = (d - timedelta(days=10)).strftime("%Y%m%d"), (d + timedelta(days=3)).strftime("%Y%m%d")
    disclosure = conn.execute(
        """SELECT report_nm FROM dart_disclosures WHERE stock_code=? AND rcept_dt BETWEEN ? AND ?
           AND (report_nm LIKE '%분할%' OR report_nm LIKE '%병합%' OR report_nm LIKE '%무상증자%'
                OR report_nm LIKE '%유상증자%' OR report_nm LIKE '%감자%' OR report_nm LIKE '%상장폐지%')
           ORDER BY rcept_dt DESC LIMIT 1""", (code, lo, hi),
    ).fetchone()
    action = conn.execute(
        """SELECT event_type,adjustment_status,evidence_report_name FROM corporate_action_events
           WHERE stock_code=? AND event_date BETWEEN ? AND ? ORDER BY CASE WHEN adjustment_status='factor_confirmed' THEN 0 ELSE 1 END, confidence DESC LIMIT 1""",
        (code, (d - timedelta(days=3)).date().isoformat(), (d + timedelta(days=3)).date().isoformat()),
    ).fetchone()
    action_date_gap = 0
    if action is None and ratio is not None:
        wide = conn.execute(
            """SELECT event_type,adjustment_status,evidence_report_name,backward_price_factor,event_date
               FROM corporate_action_events
               WHERE stock_code=? AND adjustment_status='factor_confirmed' AND backward_price_factor IS NOT NULL
               AND event_date BETWEEN ? AND ?""",
            (code, (d - timedelta(days=25)).date().isoformat(), (d + timedelta(days=25)).date().isoformat()),
        ).fetchall()
        best = None
        for w in wide:
            bpf = w[3]
            if not bpf:
                continue
            for candidate in (bpf, 1.0 / bpf):
                if abs(candidate - ratio) / max(abs(ratio), 0.01) <= 0.05:
                    gap = abs((datetime.strptime(str(w[4])[:10], "%Y-%m-%d") - d).days)
                    if best is None or gap < best[0]:
                        best = (gap, w)
                    break
        if best is not None:
            action = best[1]
            action_date_gap = best[0]

    if not (code.isdigit() and len(code) == 6):
        classification, usable = "non_equity_symbol", 0
        evidence = "Index/macro symbol mixed into price_history"
    elif quality_status in ("coverage_gap", "invalid_ohlcv", "invalid_previous_price", "quarantined_basis"):
        classification, usable = quality_status, 0
        evidence = "Structural price safety check; quote agreement cannot override"
    elif action and action[1] == "factor_confirmed":
        classification, usable = "confirmed_corporate_action", 0
        evidence = f"Confirmed normalized event: {action[0]}"
        if action_date_gap:
            evidence += f" (matched via ratio, {action_date_gap}d from recorded event_date)"
    elif action:
        classification, usable = "corporate_action_pending_confirmation", 0
        evidence = f"Unconfirmed matched event ({action[1]}): {action[0]}"
    elif disclosure:
        classification, usable = "corporate_action_or_delisting_nearby", 0
        evidence = f"Nearby disclosure: {disclosure[0]}"
    elif raw_ratio is not None and ratio is not None and abs(raw_ratio - ratio) / max(abs(ratio), 0.01) <= 0.005:
        classification, usable = "raw_source_confirmed_jump_review", 0
        evidence = f"Public raw series confirms ratio {raw_ratio:.4f}"
    elif raw_ratio is not None and not outside_band(raw_prev, raw_cur, event_date):
        classification, usable = "mixed_basis_or_price_corruption", 0
        evidence = f"price_history ratio {ratio:.4f}, raw ratio {raw_ratio:.4f}"
    elif code not in current_common:
        classification, usable = "inactive_or_noncommon_review", 0
        evidence = "Not in latest active KOSPI/KOSDAQ common-stock universe"
    else:
        classification, usable = "unresolved_active_common", 0
        evidence = "No same-day raw confirmation or nearby capital-action evidence"

    return ("update", {
        "previous_date": previous_date, "previous_close": previous_close, "event_close": close,
        "price_ratio": ratio, "public_previous_close": raw_prev, "public_event_close": raw_cur,
        "public_price_ratio": raw_ratio, "classification": classification, "return_usable": usable,
        "matched_event_type": action[0] if action else None,
        "matched_report_name": (action[2] if action else None) or (disclosure[0] if disclosure else None),
        "evidence": evidence,
    })


def run(dry_run: bool) -> dict:
    conn = connect_primary_db(timeout=300)
    try:
        stale = find_stale(conn)
        current_common = set(r[0] for r in conn.execute(
            """WITH x AS (SELECT *,ROW_NUMBER() OVER(PARTITION BY stock_code ORDER BY base_date DESC,id DESC) rn FROM stock_universe)
               SELECT stock_code FROM x WHERE rn=1 AND market IN ('KOSPI','KOSDAQ') AND COALESCE(stock_type,'보통주')='보통주'"""
        ))

        result = {"stale_found": len(stale), "dry_run": dry_run, "deleted": 0, "updated": 0,
                   "reclassified_into": {}}
        if dry_run:
            return result

        now = datetime.now().isoformat(timespec="seconds")
        for code, event_date in stale:
            action, payload = classify_one(conn, code, event_date, current_common)
            if action == "delete":
                conn.execute(
                    "DELETE FROM price_jump_audit WHERE stock_code=? AND event_date=?",
                    (code, event_date),
                )
                result["deleted"] += 1
            else:
                conn.execute(
                    """UPDATE price_jump_audit SET previous_date=?, previous_close=?, event_close=?,
                       price_ratio=?, public_previous_close=?, public_event_close=?, public_price_ratio=?,
                       classification=?, return_usable=?, matched_event_type=?, matched_report_name=?,
                       evidence=?, audited_at=? WHERE stock_code=? AND event_date=?""",
                    (payload["previous_date"], payload["previous_close"], payload["event_close"],
                     payload["price_ratio"], payload["public_previous_close"], payload["public_event_close"],
                     payload["public_price_ratio"], payload["classification"], payload["return_usable"],
                     payload["matched_event_type"], payload["matched_report_name"], payload["evidence"],
                     now, code, event_date),
                )
                result["updated"] += 1
                result["reclassified_into"][payload["classification"]] = (
                    result["reclassified_into"].get(payload["classification"], 0) + 1
                )

        conn.execute(
            """INSERT INTO data_fix_log
               (fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,
                new_value_summary,source,run_id)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                now, "price_jump_audit", "stale audit row reclassification after bulk price fix",
                result["deleted"] + result["updated"],
                "DELETE where corrected data is no longer a jump; else re-run classification logic "
                "against corrected price_history values",
                "stale previous_close/event_close left over from pre-fix price_history",
                "re-classification using price_history_quality_v (live, post-fix) + same rules as "
                "audit_price_jumps_and_build_canonical.py",
                "follow-up to bulk_marcap_interpolation_repair_2026 (2026-09-23)",
                f"reclassify_stale_price_jump_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            ),
        )
        conn.commit()
        return result
    finally:
        conn.close()


if __name__ == "__main__":
    import json
    print(json.dumps(run(dry_run="--apply" not in sys.argv), ensure_ascii=False, indent=2))
