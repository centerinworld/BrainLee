"""Schema-compatible entry point for the per-ETF daily rebalance audit."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import daily_rebalance_audit as audit
from full_pdf_collector import DB_PATH, connect


def sync_fallback_compatibility(conn: sqlite3.Connection, day: str) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS etf_pdf_issuer_fallback_snapshot (
            base_date TEXT NOT NULL,
            etf_ticker TEXT NOT NULL,
            source TEXT NOT NULL,
            effective_date TEXT NOT NULL,
            status TEXT NOT NULL,
            component_count INTEGER NOT NULL,
            PRIMARY KEY(base_date,etf_ticker)
        )
        """
    )
    if conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='etf_pdf_issuer_fallback'"
    ).fetchone()[0]:
        with conn:
            conn.execute(
                "DELETE FROM etf_pdf_issuer_fallback_snapshot WHERE base_date=?", (day,)
            )
            conn.execute(
                """
                INSERT INTO etf_pdf_issuer_fallback_snapshot(
                    base_date,etf_ticker,source,effective_date,status,component_count
                )
                SELECT base_date,etf_ticker,source,effective_date,status,component_count
                FROM etf_pdf_issuer_fallback WHERE base_date=?
                """,
                (day,),
            )


def audit_day(conn: sqlite3.Connection, day: str):
    sync_fallback_compatibility(conn, day)
    return audit.audit_day(conn, day)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date")
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()
    conn = connect(Path(args.db))
    day = args.date or conn.execute(
        "SELECT MAX(base_date) FROM etf_pdf_full_snapshot"
    ).fetchone()[0]
    if not day:
        raise RuntimeError("No ETF PDF snapshot is available")
    print(json.dumps(audit_day(conn, day),ensure_ascii=False,indent=2))
    conn.close()


if __name__ == "__main__":
    main()
