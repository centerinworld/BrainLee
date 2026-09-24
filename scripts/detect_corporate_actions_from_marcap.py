#!/usr/bin/env python3
"""Detect real shares-outstanding discontinuities from marcap (2026-09-21) and
register them in corporate_action_events, for stocks/periods our own
shares-outstanding tracking doesn't cover (marcap goes back to 1995; our own
`stock_price_daily`-driven detector in build_corporate_action_adjustment_engine.py
starts later).

Classification/status logic is copied verbatim from that script's
`stock_price_daily_shares` source so results are directly comparable and
equally conservative: an unclassified share-count jump is recorded with
adjustment_status='review_required' (or 'not_price_adjusting' if <5%) and
backward_price_factor left NULL. It never becomes 'factor_confirmed' - and
therefore never feeds an automatic price adjustment anywhere - without a
matching DART disclosure, exactly like the existing detector. This script
only supplies evidence; it does not decide adjustability.

Initial run scope: the 8 stock codes from the 2026-09-21 invalid_ohlcv
investigation (hermes.md, "invalid_ohlcv 38건 중 27건 완결") whose flagged
dates showed a pykrx/FinanceDataReader adjusted-close mismatch against our own
raw price_history. marcap's own (unadjusted) Close matched our raw value
exactly for all 8 at their flagged dates - confirming our data was right - but
that left the *source* of pykrx's mismatched adjustment factor unexplained.
This script's shares-history scan is how that got resolved for at least
016385 (KG스틸우): a real ~4x reduction on 2015-03-04 (and another ~4x on
2016-05-25) exists in marcap, but pykrx's own 2012-era adjustment factor was
~196x - not explained by any real event this scan finds. That's independent
evidence pykrx's own adjustment computation (not a basis difference) is the
outlier here, matching publicly reported pykrx issues (GitHub #89, #162) about
adjusted-price inconsistencies between its two upstream sources.

--codes accepts a comma-separated list (default: the 8 above); --start-year/
--end-year bound the marcap scan (default 1995-2018, i.e. before our own
shares-outstanding tracking is expected to have coverage).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402
from marcap_client import shares_history  # noqa: E402

DEFAULT_CODES = ["016385", "001529", "003945", "004790", "011720", "030790", "040670", "099660"]


def _classify(ratio: float) -> tuple[str, float]:
    if ratio >= 1.5:
        return "share_increase_unclassified", 0.55
    if ratio <= 0.67:
        return "share_reduction_unclassified", 0.55
    if ratio > 1:
        return "rights_or_other_issue", 0.45
    return "reduction_or_cancellation", 0.45


def find_events(code: str, start_year: int, end_year: int) -> list[dict]:
    df = shares_history(code, start_year, end_year)
    events = []
    prev_shares = None
    for _, row in df.iterrows():
        shares = float(row["Stocks"]) if row["Stocks"] else None
        if shares and prev_shares and abs(shares / prev_shares - 1) >= 0.01:
            ratio = shares / prev_shares
            event_type, confidence = _classify(ratio)
            minor_change = 0.95 <= ratio <= 1.05
            status = "not_price_adjusting" if minor_change else "review_required"
            events.append({
                "stock_code": code, "event_date": str(row["Date"]), "event_type": event_type,
                "old_shares": prev_shares, "new_shares": shares, "share_ratio": ratio,
                "backward_price_factor": None, "source": "marcap_shares_daily",
                "confidence": confidence, "adjustment_status": status,
                "note": (
                    "Share change is within 5%; retained for dilution review but no "
                    "discontinuity factor is required." if minor_change else
                    "No automatic price rewrite; event economics or type is not "
                    "sufficiently confirmed (marcap gives share counts only, no filing)."
                ),
            })
        if shares:
            prev_shares = shares
    return events


def run(codes: list[str], start_year: int, end_year: int, dry_run: bool) -> dict:
    all_events = []
    for code in codes:
        all_events.extend(find_events(code, start_year, end_year))

    conn = connect_primary_db(timeout=60)
    inserted, skipped = 0, 0
    now = datetime.now().isoformat(timespec="seconds")
    for ev in all_events:
        existing = conn.execute(
            "SELECT 1 FROM corporate_action_events WHERE stock_code=? AND event_date=? AND event_type=?",
            (ev["stock_code"], ev["event_date"], ev["event_type"]),
        ).fetchone()
        if existing:
            skipped += 1
            continue
        inserted += 1
        if dry_run:
            continue
        conn.execute(
            """INSERT INTO corporate_action_events(
                 stock_code,event_date,event_type,old_shares,new_shares,share_ratio,backward_price_factor,
                 evidence_report_name,evidence_rcept_no,evidence_url,source,confidence,adjustment_status,note,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ev["stock_code"], ev["event_date"], ev["event_type"], ev["old_shares"], ev["new_shares"],
             ev["share_ratio"], ev["backward_price_factor"], None, None, None,
             ev["source"], ev["confidence"], ev["adjustment_status"], ev["note"], now, now),
        )
    if not dry_run:
        conn.commit()
    conn.close()
    return {"found": len(all_events), "inserted": inserted, "already_present": skipped,
            "dry_run": dry_run, "events": all_events}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--codes", default=",".join(DEFAULT_CODES))
    parser.add_argument("--start-year", type=int, default=1995)
    parser.add_argument("--end-year", type=int, default=2018)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    result = run(codes, args.start_year, args.end_year, dry_run=not args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
