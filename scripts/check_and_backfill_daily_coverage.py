#!/usr/bin/env python3
"""Same-day completeness check + safe backfill for price_history (root-cause
fix for the 'coverage_gap' pool, 2026-09-19).

Root cause found this session: coverage_gap (17,138 rows total, the single
largest unresolved category in price_jump_audit) is heavily concentrated on
specific calendar dates rather than spread evenly - e.g. 2026-07-20 alone
accounts for 2,689 rows, 2024-11-06 for 786, 2024-10-31 for 783,
2024-08-28 for 749, 2024-01-19 for 656. That shape (hundreds of stocks
missing the exact same single day, repeated on ~1,400 distinct dates over
the full history) is not consistent with genuine per-stock trading halts,
which would be rare and scattered - it is consistent with the daily
collector partially failing for a batch of stocks on those specific days
(network blip, rate limit, timeout) with nothing that ever went back and
noticed which stocks got skipped.

Confirmed the gap in the pipeline: scheduler.py's _job_kis_ohlcv_daily runs
collect_kis_ohlcv.py as a subprocess and only checks the process-level
returncode (raise RuntimeError if nonzero) - it has no visibility into
whether the collector silently skipped a subset of stocks internally while
still exiting 0 overall. collection_health.py's DatasetContract for
price_history only checks AGGREGATE coverage on the latest date
(min_latest_coverage=2000 distinct stock_code) - a run that gets, say,
1,700 of ~1,885 candidate stocks can still individually miss ~185 specific
stocks forever without ever tripping that aggregate threshold, and nothing
records *which* stocks were missed. There is no per-stock completeness
check anywhere in this codebase (grepped for it before writing this).

This script is that missing check. It is intentionally a separate script
rather than a change to collect_naver_ohlcv_today.py or collect_kis_ohlcv.py
(both already have unrelated uncommitted local changes from another session
mid-migration to price_integrity gating - not touching those files avoids
colliding with in-progress work). Scheduler wiring: see
scheduler.py's _loop_daily_coverage_backfill / _job_daily_coverage_backfill,
scheduled ~19:15 daily, after _loop_kis_daily's 18:00 run (which has up to a
1-hour subprocess timeout) has had time to finish either way.

Safety: every backfilled row goes through price_integrity.gate_gap_fill_row
(already existed in this codebase for exactly this purpose - validates the
value against invalid_ohlcv() and the price-limit band of the neighboring
trading days before allowing the write, quarantining anything it can't
confirm rather than guessing). No existing row is ever overwritten - INSERT
only, and only for a stock_code+date combination that has zero row today.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402
from price_integrity import ensure_schema, gate_gap_fill_row  # noqa: E402
from collect_naver_ohlcv_today import get_naver_ohlcv, make_session  # noqa: E402


def find_missing_codes(conn, expected_date: str) -> list[str]:
    """Universe codes with zero price_history row on expected_date."""
    universe = {
        r[0] for r in conn.execute(
            "SELECT stock_code FROM stock_universe "
            "WHERE market IN ('KOSPI','KOSDAQ','유가증권','코스닥') AND LENGTH(stock_code)=6 "
            "AND stock_code ~ '^[0-9A-Z]{6}$'"
        )
    }
    covered = {
        r[0] for r in conn.execute(
            "SELECT stock_code FROM price_history WHERE date=?", (expected_date,)
        )
    }
    return sorted(universe - covered)


def run(expected_date: str | None = None, dry_run: bool = True) -> dict:
    expected_date = expected_date or date.today().isoformat()
    conn = connect_primary_db(timeout=180)
    session = make_session()
    try:
        ensure_schema(conn)
        missing = find_missing_codes(conn, expected_date)
        result = {
            "expected_date": expected_date, "missing_count": len(missing),
            "backfilled": [], "quarantined": [], "no_naver_data": [],
            "dry_run": dry_run,
        }
        if not missing:
            return result

        for code in missing:
            rows = get_naver_ohlcv(session, code, count=5)
            match = next((r for r in rows if r[0] == expected_date), None)
            if not match:
                result["no_naver_data"].append(code)
                time.sleep(0.3)
                continue
            _, o, h, l, c, v = match
            if dry_run:
                result["backfilled"].append({"code": code, "close": c})
                time.sleep(0.3)
                continue
            accepted = gate_gap_fill_row(conn, code, expected_date, (o, h, l, c, v), "daily_coverage_backfill")
            if accepted:
                conn.execute(
                    "INSERT INTO price_history (stock_code,date,open,high,low,close,volume) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (code, expected_date, o, h, l, c, v),
                )
                conn.commit()
                result["backfilled"].append({"code": code, "close": c})
            else:
                result["quarantined"].append(code)
            time.sleep(0.3)
        return result
    finally:
        conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=None, help="YYYY-MM-DD, default today")
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    print(json.dumps(run(expected_date=args.date, dry_run=not args.apply), ensure_ascii=False, indent=2))
