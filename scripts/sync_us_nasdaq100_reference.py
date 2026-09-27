#!/usr/bin/env python3
"""Synchronize reconstructed point-in-time Nasdaq-100 membership.

The public source reconstructs effective-date snapshots from cited component
changes.  It is useful for survivorship-bias control but is not a licensed
Nasdaq constituent feed.  The default is read-only; ``--apply`` replaces only
this source's Nasdaq-100 rows and records the URL, digest and row counts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db
from scripts.sync_us_backtest_reference import (
    derive_events,
    derive_intervals,
    ensure_schema,
    parse_snapshots,
    upsert_verified_aliases,
)


INDEX_NAME = "NASDAQ100"
SOURCE = "github_thuningxu_nasdaq100"
SNAPSHOT_URL = (
    "https://raw.githubusercontent.com/thuningxu/sp500nq100/main/"
    "nasdaq100_components_history.csv"
)


def fetch(url: str = SNAPSHOT_URL) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "stock-dashboard-research/1.0"})
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.read()


def validated_snapshots(payload: bytes) -> list[tuple[str, set[str]]]:
    snapshots = parse_snapshots(payload, min_constituents=90)
    for day, members in snapshots:
        if len(members) > 120:
            raise ValueError(f"implausible Nasdaq-100 snapshot: {day} ({len(members)} tickers)")
    return snapshots


def apply_reference(payload: bytes) -> dict:
    snapshots = validated_snapshots(payload)
    intervals = derive_intervals(snapshots)
    events = derive_events(snapshots)
    digest = hashlib.sha256(payload).hexdigest()
    collected = datetime.now(timezone.utc).isoformat()
    reference_as_of = datetime.now(timezone.utc).date().isoformat()
    run_id = f"usref_{uuid.uuid4().hex}"
    conn = connect_primary_db(timeout=120)
    try:
        ensure_schema(conn)
        alias_count = upsert_verified_aliases(conn, collected)
        conn.execute(
            "DELETE FROM us_index_membership_intervals WHERE index_name=? AND source=?",
            (INDEX_NAME, SOURCE),
        )
        conn.execute(
            "DELETE FROM us_index_constituent_events WHERE index_name=? AND source=?",
            (INDEX_NAME, SOURCE),
        )
        conn.executemany(
            """INSERT INTO us_index_membership_intervals
            (index_name,ticker,ticker_raw,effective_from,effective_to,source,source_hash,
             quality_status,collected_at) VALUES(?,?,?,?,?,?,?,?,?)""",
            [
                (INDEX_NAME, x.ticker, x.ticker_raw, x.effective_from, x.effective_to,
                 SOURCE, digest, "public_reconstructed", collected)
                for x in intervals
            ],
        )
        conn.executemany(
            """INSERT INTO us_index_constituent_events
            (index_name,effective_date,action,ticker,ticker_raw,source,source_hash,collected_at)
            VALUES(?,?,?,?,?,?,?,?)""",
            [
                (INDEX_NAME, day, action, ticker, raw, SOURCE, digest, collected)
                for day, action, raw, ticker in events
            ],
        )
        conn.execute(
            """INSERT INTO us_reference_source_runs
            (run_id,source,source_url,source_hash,collected_at,status,first_effective_date,
             last_effective_date,snapshot_count,interval_count,event_count,note,reference_as_of)
            VALUES(?,?,?,?,?,'success',?,?,?,?,?,?,?)""",
            (run_id, SOURCE, SNAPSHOT_URL, digest, collected, snapshots[0][0], snapshots[-1][0],
             len(snapshots), len(intervals), len(events),
             "Public reconstructed Nasdaq-100 history; not a licensed Nasdaq feed",
             reference_as_of),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    verify = connect_primary_db(readonly=True, timeout=120)
    try:
        interval_count = verify.execute(
            "SELECT COUNT(*) FROM us_index_membership_intervals WHERE index_name=? AND source=?",
            (INDEX_NAME, SOURCE),
        ).fetchone()[0]
        event_count = verify.execute(
            "SELECT COUNT(*) FROM us_index_constituent_events WHERE index_name=? AND source=?",
            (INDEX_NAME, SOURCE),
        ).fetchone()[0]
    finally:
        verify.close()
    if interval_count != len(intervals) or event_count != len(events):
        raise RuntimeError("post-write count verification failed")
    return {
        "run_id": run_id,
        "source_hash": digest,
        "first": snapshots[0][0],
        "last": snapshots[-1][0],
        "reference_as_of": reference_as_of,
        "snapshots": len(snapshots),
        "intervals": interval_count,
        "events": event_count,
        "verified_aliases": alias_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--input", help="Use a local CSV instead of downloading")
    args = parser.parse_args()
    payload = Path(args.input).read_bytes() if args.input else fetch()
    snapshots = validated_snapshots(payload)
    summary = {
        "source_hash": hashlib.sha256(payload).hexdigest(),
        "first": snapshots[0][0],
        "last": snapshots[-1][0],
        "snapshots": len(snapshots),
        "intervals": len(derive_intervals(snapshots)),
        "events": len(derive_events(snapshots)),
        "apply": args.apply,
    }
    if args.apply:
        summary = apply_reference(payload)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
