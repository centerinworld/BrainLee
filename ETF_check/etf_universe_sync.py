"""Build a validated, date-pinned universe for the full KRX ETF PDF collector."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from direct_etf_pipeline import KISETFSource


MIN_UNIVERSE_SIZE = 100
MAX_UNIVERSE_CHANGE_RATIO = 0.10


def initialize(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS etf_universe_daily (
            base_date TEXT NOT NULL,
            etf_ticker TEXT NOT NULL,
            etf_name TEXT NOT NULL,
            market TEXT NOT NULL,
            isin TEXT NOT NULL,
            listed_date TEXT NOT NULL DEFAULT '',
            listed_shares REAL,
            source TEXT NOT NULL,
            collected_at TEXT NOT NULL,
            PRIMARY KEY(base_date, etf_ticker)
        );
        CREATE INDEX IF NOT EXISTS idx_etf_universe_daily_date
            ON etf_universe_daily(base_date);
        CREATE TABLE IF NOT EXISTS etf_universe_sync_run (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            base_date TEXT NOT NULL,
            status TEXT NOT NULL,
            previous_count INTEGER,
            current_count INTEGER,
            added_count INTEGER,
            removed_count INTEGER,
            universe_hash TEXT,
            details_json TEXT NOT NULL,
            collected_at TEXT NOT NULL
        );
        """
    )


def _hash(rows: list[Any]) -> str:
    payload = "\n".join(f"{row.ticker}|{row.isin}|{row.name}" for row in rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sync_universe(
    conn: sqlite3.Connection,
    base_date: str,
    source: KISETFSource | None = None,
) -> dict[str, Any]:
    initialize(conn)
    rows = sorted((source or KISETFSource()).universe(), key=lambda row: row.ticker)
    tickers = {row.ticker for row in rows}
    if len(rows) < MIN_UNIVERSE_SIZE or len(tickers) != len(rows):
        raise RuntimeError(
            f"ETF universe validation failed: rows={len(rows)}, unique={len(tickers)}"
        )

    previous_date = conn.execute(
        "SELECT MAX(base_date) FROM etf_universe_daily WHERE base_date < ?",
        (base_date,),
    ).fetchone()[0]
    previous = set()
    if previous_date:
        previous = {
            row[0]
            for row in conn.execute(
                "SELECT etf_ticker FROM etf_universe_daily WHERE base_date=?",
                (previous_date,),
            )
        }
    elif conn.execute("SELECT COUNT(*) FROM etf_meta WHERE is_active=1").fetchone()[0]:
        previous = {
            row[0]
            for row in conn.execute(
                "SELECT etf_ticker FROM etf_meta WHERE is_active=1"
            )
        }

    added, removed = sorted(tickers - previous), sorted(previous - tickers)
    ratio = (len(added) + len(removed)) / max(len(previous), 1)
    details = {
        "previous_date": previous_date,
        "added": added,
        "removed": removed,
        "change_ratio": ratio,
        "source": "KIS_MASTER",
    }
    now = datetime.now().isoformat(timespec="seconds")
    digest = _hash(rows)
    if previous and ratio > MAX_UNIVERSE_CHANGE_RATIO:
        with conn:
            conn.execute(
                """
                INSERT INTO etf_universe_sync_run(
                    base_date,status,previous_count,current_count,added_count,
                    removed_count,universe_hash,details_json,collected_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    base_date,"rejected",len(previous),len(rows),len(added),
                    len(removed),digest,json.dumps(details,ensure_ascii=False),now,
                ),
            )
        raise RuntimeError(
            f"ETF universe changed abnormally: {len(previous)} -> {len(rows)} ({ratio:.1%})"
        )

    with conn:
        conn.execute("DELETE FROM etf_universe_daily WHERE base_date=?", (base_date,))
        conn.executemany(
            """
            INSERT INTO etf_universe_daily(
                base_date,etf_ticker,etf_name,market,isin,listed_date,
                listed_shares,source,collected_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    base_date,row.ticker,row.name,row.market,row.isin,row.listed_date,
                    row.listed_shares,"KIS_MASTER",now,
                )
                for row in rows
            ],
        )
        conn.execute("UPDATE etf_meta SET is_active=0")
        conn.executemany(
            """
            INSERT INTO etf_meta(
                etf_ticker,etf_name,market,isin,listed_date,listed_shares,
                universe_source,is_active,updated_at
            ) VALUES(?,?,?,?,?,?,?,1,?)
            ON CONFLICT(etf_ticker) DO UPDATE SET
                etf_name=excluded.etf_name,market=excluded.market,
                isin=excluded.isin,listed_date=excluded.listed_date,
                listed_shares=excluded.listed_shares,
                universe_source=excluded.universe_source,is_active=1,
                updated_at=excluded.updated_at
            """,
            [
                (
                    row.ticker,row.name,row.market,row.isin,row.listed_date,
                    row.listed_shares,"KIS_MASTER",now,
                )
                for row in rows
            ],
        )
        conn.execute(
            """
            INSERT INTO etf_universe_sync_run(
                base_date,status,previous_count,current_count,added_count,
                removed_count,universe_hash,details_json,collected_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                base_date,"complete",len(previous),len(rows),len(added),
                len(removed),digest,json.dumps(details,ensure_ascii=False),now,
            ),
        )
    return {
        "base_date":base_date,"count":len(rows),"previous_count":len(previous),
        "added":added,"removed":removed,"universe_hash":digest,
    }


def dated_universe(conn: sqlite3.Connection, base_date: str) -> list[tuple[str, str, str]]:
    initialize(conn)
    rows = conn.execute(
        """
        SELECT etf_ticker,etf_name,isin FROM etf_universe_daily
        WHERE base_date=? AND LENGTH(isin)=12 ORDER BY etf_ticker
        """,
        (base_date,),
    ).fetchall()
    if not rows:
        raise RuntimeError(f"No validated ETF universe for {base_date}")
    return [(row[0], row[1], row[2]) for row in rows]
