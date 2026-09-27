#!/usr/bin/env python3
"""Apply official S&P 500 changes missing from the reconstructed source.

Dry-run is the default.  The three September 2026 replacements are taken from
the linked S&P Global announcement and become effective before the 2026-09-21
open.  The write is idempotent and independently read back.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db


INDEX = "S&P500"
EFFECTIVE = "2026-09-21"
AS_OF = "2026-09-26"
SOURCE_URL = (
    "https://press.spglobal.com/2026-09-04-Bloom-Energy,-Illumina,-and-"
    "Everpure-Set-to-Join-S-P-500-Others-to-Join-S-P-100,-S-P-MidCap-400,-"
    "and-S-P-SmallCap-600"
)
EVENTS = (
    ("BE", "add"), ("TAP", "remove"),
    ("P", "add"), ("TTD", "remove"),
    ("ILMN", "add"), ("BLDR", "remove"),
)


def apply() -> dict:
    run_id = f"usidxevt_{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc).isoformat()
    conn = connect_primary_db(timeout=120)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS us_index_membership_verified_events (
            index_name TEXT NOT NULL, ticker TEXT NOT NULL, effective_date TEXT NOT NULL,
            action TEXT NOT NULL, status TEXT NOT NULL, source_url TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            PRIMARY KEY(index_name,ticker,effective_date,action))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS us_index_membership_event_runs (
            run_id TEXT PRIMARY KEY, index_name TEXT NOT NULL, as_of_date TEXT NOT NULL,
            status TEXT NOT NULL, source_url TEXT NOT NULL, event_count INTEGER NOT NULL,
            recorded_at TEXT NOT NULL)""")
        conn.executemany("""INSERT INTO us_index_membership_verified_events
            (index_name,ticker,effective_date,action,status,source_url,recorded_at)
            VALUES(?,?,?,?,'verified',?,?)
            ON CONFLICT(index_name,ticker,effective_date,action) DO UPDATE SET
              status=excluded.status,source_url=excluded.source_url,recorded_at=excluded.recorded_at""",
            [(INDEX, ticker, EFFECTIVE, action, SOURCE_URL, now) for ticker, action in EVENTS])
        conn.execute("""INSERT INTO us_index_membership_event_runs
            (run_id,index_name,as_of_date,status,source_url,event_count,recorded_at)
            VALUES(?,?,?,'success',?,?,?)""",
            (run_id, INDEX, AS_OF, SOURCE_URL, len(EVENTS), now))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    verify = connect_primary_db(readonly=True, timeout=120)
    try:
        count = verify.execute("""SELECT COUNT(*) FROM us_index_membership_verified_events
            WHERE index_name=? AND effective_date=? AND status='verified'""",
            (INDEX, EFFECTIVE)).fetchone()[0]
        as_of = verify.execute("""SELECT MAX(as_of_date) FROM us_index_membership_event_runs
            WHERE index_name=? AND status='success'""", (INDEX,)).fetchone()[0]
    finally:
        verify.close()
    if count < len(EVENTS) or str(as_of) < AS_OF:
        raise RuntimeError("official event post-write verification failed")
    return {"run_id": run_id, "events_verified": len(EVENTS), "as_of": as_of}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = {"apply": args.apply, "effective_date": EFFECTIVE, "as_of": AS_OF,
              "events": EVENTS, "source_url": SOURCE_URL}
    if args.apply:
        result = apply()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
