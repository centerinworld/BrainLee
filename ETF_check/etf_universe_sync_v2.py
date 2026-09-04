"""Reuse validated historical ETF universes instead of rewriting them on retries."""
from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from etf_universe_sync import dated_universe, initialize, sync_universe


def existing_universe(conn: sqlite3.Connection, base_date: str) -> dict[str, Any] | None:
    initialize(conn)
    rows = conn.execute(
        """
        SELECT etf_ticker,etf_name,isin FROM etf_universe_daily
        WHERE base_date=? ORDER BY etf_ticker
        """,
        (base_date,),
    ).fetchall()
    if not rows:
        return None
    payload = "\n".join(f"{row[0]}|{row[2]}|{row[1]}" for row in rows)
    return {
        "base_date":base_date,"count":len(rows),"reused":True,
        "universe_hash":hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }


def get_or_sync_universe(conn: sqlite3.Connection, base_date: str) -> dict[str, Any]:
    return existing_universe(conn, base_date) or sync_universe(conn, base_date)


__all__ = ["dated_universe", "existing_universe", "get_or_sync_universe"]
