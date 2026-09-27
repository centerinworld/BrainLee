#!/usr/bin/env python3
"""Synchronize point-in-time US index membership reference data.

The free source is a reconstructed S&P 500 history, not an S&P licensed feed.
Every apply stores the source URL, SHA-256 and collection result so backtests
can distinguish reproducible public evidence from an official index feed.

Default is dry-run.  ``--apply`` replaces only rows for the same index/source,
inside one transaction, then independently reads the committed counts back.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db


INDEX_NAME = "S&P500"
SOURCE = "github_fja05680_sp500"
SNAPSHOT_URL = (
    "https://raw.githubusercontent.com/fja05680/sp500/master/"
    "S%26P%20500%20Historical%20Components%20%26%20Changes%20%28Updated%29.csv"
)

# Only same-security ticker changes supported by issuer or SEC evidence belong
# here.  Acquisitions, mergers with an exchange ratio, and spin-offs must be
# represented as terminal security outcomes instead of aliases.
VERIFIED_TICKER_ALIASES = (
    ("ABC", "COR", "2023-08-30", "https://investor.cencora.com/news/news-details/2023/AmerisourceBergen-becomes-Cencora-in-alignment-with-the-companys-growing-global-footprint-and-central-role-in-pharmaceutical-access-and-care/default.aspx"),
    ("ANTM", "ELV", "2022-06-28", "https://ir.elevancehealth.com/events-and-presentations/event-details/2022/Corporate-Rebranding-to-New-Name-Elevance-Health-Inc/default.aspx"),
    ("BLL", "BALL", "2022-05-10", "https://investors.ball.com/news-presentations/press-releases/detail/82/ball-board-declares-quarterly-dividend-stock-ticker-symbol-changing-to-ball"),
    ("FB", "META", "2022-06-09", "https://investor.atmeta.com/investor-news/press-release-details/2022/Meta-Platforms-Inc.-to-Change-Ticker-Symbol-to-META-on-June-9/default.aspx"),
    ("VIAC", "PARA", "2022-02-17", "https://ir.paramount.com/static-files/e3e66501-aab1-45ee-b70e-41cc592016d7"),
    ("WLTW", "WTW", "2022-01-10", "https://investors.wtwco.com/news-releases/news-release-details/willis-towers-watson-announces-nasdaq-ticker-symbol-change-wltw"),
    ("NLOK", "GEN", "2022-11-08", "https://investor.gendigital.com/news/news-details/2022/Introducing-Gen-The-Company-to-Power-Digital-Freedom/default.aspx/1000/"),
    ("PKI", "RVTY", "2023-05-16", "https://ir.revvity.com/news/investor-news/news-details/2023/Launching-Revvity-A-Scientific-Solutions-Company-Powering-Innovation-from-Discovery-to-Cure/default.aspx"),
    ("RE", "EG", "2023-07-10", "https://investors.everestglobal.com/news/news-details/2023/Everest-to-Rebrand-Company-Name-and-NYSE-Ticker-to-Reflect-its-Evolution-Global-Growth-and-Diversification-Strategy/default.aspx"),
    ("FLT", "CPAY", "2024-03-25", "https://investor.corpay.com/news-releases/news-release-details/fleetcor-announces-rebranding-corpay"),
    ("CDAY", "DAY", "2024-02-01", "https://investors.dayforce.com/news-and-events/press-releases/press-release-details/2024/Ceridian-to-change-ticker-symbol-to-DAY-on-NYSE-and-TSX-effective-February-1/default.aspx"),
    ("FI", "FISV", "2025-11-11", "https://investors.fiserv.com/news-releases/news-release-details/fiserv-announces-transfer-stock-exchange-listing-nasdaq"),
    ("MMC", "MRSH", "2026-01-14", "https://www.sec.gov/Archives/edgar/data/62709/000006270926000022/mrsh-20251231.htm"),
    ("BK", "BNY", "2026-05-21", "https://www.bny.com/corporate/global/en/about-us/newsroom/press-release/bny-announces-planned-change-of-stock-ticker-symbol-to-bny-130465.html"),
    ("PSTG", "P", "2026-04-17", "https://www.everpuredata.com/uk/company/newsroom/press-releases/everpure-to-change-ticker-symbol.html"),
)


@dataclass(frozen=True)
class MembershipInterval:
    ticker_raw: str
    ticker: str
    effective_from: str
    effective_to: str | None  # exclusive


def canonical_yahoo_ticker(value: str) -> str:
    """Normalize share-class punctuation to the existing Yahoo price key."""
    return value.strip().upper().replace(".", "-")


def parse_snapshots(payload: bytes, *, min_constituents: int = 400) -> list[tuple[str, set[str]]]:
    text = payload.decode("utf-8-sig")
    rows: list[tuple[str, set[str]]] = []
    for row in csv.DictReader(io.StringIO(text)):
        day = str(row.get("date") or "")[:10]
        tickers = {x.strip().upper() for x in str(row.get("tickers") or "").split(",") if x.strip()}
        if not day or len(tickers) < min_constituents:
            raise ValueError(f"invalid constituent snapshot: {day} ({len(tickers)} tickers)")
        rows.append((day, tickers))
    if not rows or rows != sorted(rows, key=lambda x: x[0]):
        raise ValueError("snapshot history is empty or not sorted")
    if len({d for d, _ in rows}) != len(rows):
        raise ValueError("duplicate snapshot dates")
    return rows


def derive_intervals(snapshots: list[tuple[str, set[str]]]) -> list[MembershipInterval]:
    """Convert effective-date snapshots into [from, to) membership intervals."""
    active: dict[str, str] = {}
    result: list[MembershipInterval] = []
    previous: set[str] = set()
    for day, current in snapshots:
        for raw in sorted(previous - current):
            result.append(MembershipInterval(raw, canonical_yahoo_ticker(raw), active.pop(raw), day))
        for raw in sorted(current - previous):
            active[raw] = day
        previous = current
    for raw, start in active.items():
        result.append(MembershipInterval(raw, canonical_yahoo_ticker(raw), start, None))
    return sorted(result, key=lambda x: (x.ticker, x.effective_from, x.ticker_raw))


def derive_events(snapshots: list[tuple[str, set[str]]]) -> list[tuple[str, str, str, str]]:
    """Return (effective_date, action, raw, canonical) rows after the baseline."""
    out: list[tuple[str, str, str, str]] = []
    previous = snapshots[0][1]
    for day, current in snapshots[1:]:
        out.extend((day, "add", x, canonical_yahoo_ticker(x)) for x in sorted(current - previous))
        out.extend((day, "remove", x, canonical_yahoo_ticker(x)) for x in sorted(previous - current))
        previous = current
    return out


def fetch(url: str = SNAPSHOT_URL) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "stock-dashboard-research/1.0"})
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.read()


def ensure_schema(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS us_index_membership_intervals (
        index_name TEXT NOT NULL, ticker TEXT NOT NULL, ticker_raw TEXT NOT NULL,
        effective_from TEXT NOT NULL, effective_to TEXT, source TEXT NOT NULL,
        source_hash TEXT NOT NULL, quality_status TEXT NOT NULL,
        collected_at TEXT NOT NULL,
        PRIMARY KEY(index_name,source,ticker_raw,effective_from))""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_us_index_membership_asof
        ON us_index_membership_intervals(index_name,effective_from,effective_to,ticker)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS us_index_constituent_events (
        index_name TEXT NOT NULL, effective_date TEXT NOT NULL, action TEXT NOT NULL,
        ticker TEXT NOT NULL, ticker_raw TEXT NOT NULL, source TEXT NOT NULL,
        source_hash TEXT NOT NULL, collected_at TEXT NOT NULL,
        PRIMARY KEY(index_name,source,effective_date,action,ticker_raw))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS us_reference_source_runs (
        run_id TEXT PRIMARY KEY, source TEXT NOT NULL, source_url TEXT NOT NULL,
        source_hash TEXT NOT NULL, collected_at TEXT NOT NULL, status TEXT NOT NULL,
        first_effective_date TEXT, last_effective_date TEXT, snapshot_count INTEGER,
        interval_count INTEGER, event_count INTEGER, note TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS us_ticker_aliases (
        old_ticker TEXT PRIMARY KEY, price_ticker TEXT NOT NULL,
        effective_date TEXT NOT NULL, identity_continuity INTEGER NOT NULL,
        status TEXT NOT NULL, source_url TEXT NOT NULL, evidence_type TEXT NOT NULL,
        note TEXT, updated_at TEXT NOT NULL)""")


def upsert_verified_aliases(conn, collected: str) -> int:
    """Store reviewed identity aliases without deleting operator-added rows."""
    conn.executemany("""INSERT INTO us_ticker_aliases
        (old_ticker,price_ticker,effective_date,identity_continuity,status,source_url,
         evidence_type,note,updated_at) VALUES(?,?,?,1,'verified',?,'issuer_or_sec',?,?)
        ON CONFLICT(old_ticker) DO UPDATE SET
          price_ticker=excluded.price_ticker,
          effective_date=excluded.effective_date,
          identity_continuity=excluded.identity_continuity,
          status=excluded.status,
          source_url=excluded.source_url,
          evidence_type=excluded.evidence_type,
          note=excluded.note,
          updated_at=excluded.updated_at""", [
        (old, new, day, url, "Same listed security; ticker-only identity alias", collected)
        for old, new, day, url in VERIFIED_TICKER_ALIASES
    ])
    return len(VERIFIED_TICKER_ALIASES)


def apply_reference(payload: bytes) -> dict:
    snapshots = parse_snapshots(payload)
    intervals = derive_intervals(snapshots)
    events = derive_events(snapshots)
    digest = hashlib.sha256(payload).hexdigest()
    collected = datetime.now(timezone.utc).isoformat()
    run_id = f"usref_{uuid.uuid4().hex}"
    conn = connect_primary_db(timeout=120)
    try:
        ensure_schema(conn)
        alias_count = upsert_verified_aliases(conn, collected)
        conn.execute("DELETE FROM us_index_membership_intervals WHERE index_name=? AND source=?",
                     (INDEX_NAME, SOURCE))
        conn.execute("DELETE FROM us_index_constituent_events WHERE index_name=? AND source=?",
                     (INDEX_NAME, SOURCE))
        conn.executemany("""INSERT INTO us_index_membership_intervals
            (index_name,ticker,ticker_raw,effective_from,effective_to,source,source_hash,
             quality_status,collected_at) VALUES(?,?,?,?,?,?,?,?,?)""", [
            (INDEX_NAME, x.ticker, x.ticker_raw, x.effective_from, x.effective_to,
             SOURCE, digest, "public_reconstructed", collected) for x in intervals
        ])
        conn.executemany("""INSERT INTO us_index_constituent_events
            (index_name,effective_date,action,ticker,ticker_raw,source,source_hash,collected_at)
            VALUES(?,?,?,?,?,?,?,?)""", [
            (INDEX_NAME, day, action, ticker, raw, SOURCE, digest, collected)
            for day, action, raw, ticker in events
        ])
        conn.execute("""INSERT INTO us_reference_source_runs
            (run_id,source,source_url,source_hash,collected_at,status,first_effective_date,
             last_effective_date,snapshot_count,interval_count,event_count,note)
            VALUES(?,?,?,?,?,'success',?,?,?,?,?,?)""",
            (run_id, SOURCE, SNAPSHOT_URL, digest, collected, snapshots[0][0], snapshots[-1][0],
             len(snapshots), len(intervals), len(events),
             "Public reconstructed S&P 500 history; not an official licensed constituent feed"))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # Independent committed read-back.
    verify = connect_primary_db(readonly=True, timeout=120)
    try:
        interval_count = verify.execute(
            "SELECT COUNT(*) FROM us_index_membership_intervals WHERE index_name=? AND source=?",
            (INDEX_NAME, SOURCE)).fetchone()[0]
        event_count = verify.execute(
            "SELECT COUNT(*) FROM us_index_constituent_events WHERE index_name=? AND source=?",
            (INDEX_NAME, SOURCE)).fetchone()[0]
    finally:
        verify.close()
    if interval_count != len(intervals) or event_count != len(events):
        raise RuntimeError("post-write count verification failed")
    return {"run_id": run_id, "source_hash": digest, "first": snapshots[0][0],
            "last": snapshots[-1][0], "snapshots": len(snapshots),
            "intervals": interval_count, "events": event_count,
            "verified_aliases": alias_count}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--input", help="Use a local CSV instead of downloading")
    args = parser.parse_args()
    payload = Path(args.input).read_bytes() if args.input else fetch()
    snapshots = parse_snapshots(payload)
    summary = {"source_hash": hashlib.sha256(payload).hexdigest(), "first": snapshots[0][0],
               "last": snapshots[-1][0], "snapshots": len(snapshots),
               "intervals": len(derive_intervals(snapshots)), "events": len(derive_events(snapshots)),
               "apply": args.apply}
    if args.apply:
        summary = apply_reference(payload)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
