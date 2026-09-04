"""Detect ETF composition changes per fund even when a different fund is incomplete."""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from full_pdf_collector import DB_PATH, connect


def initialize(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS etf_pdf_rebalance_event (
            base_date TEXT NOT NULL,
            previous_date TEXT NOT NULL,
            etf_ticker TEXT NOT NULL,
            component_code TEXT NOT NULL,
            component_name TEXT NOT NULL DEFAULT '',
            change_type TEXT NOT NULL,
            previous_shares REAL,
            current_shares REAL,
            share_change REAL,
            previous_weight REAL,
            current_weight REAL,
            weight_change REAL,
            PRIMARY KEY(base_date,etf_ticker,component_code)
        );
        CREATE INDEX IF NOT EXISTS idx_etf_rebalance_component
            ON etf_pdf_rebalance_event(base_date,component_code);
        CREATE TABLE IF NOT EXISTS etf_pdf_rebalance_audit (
            base_date TEXT PRIMARY KEY,
            universe_count INTEGER NOT NULL,
            compared_etf_count INTEGER NOT NULL,
            no_prior_etf_count INTEGER NOT NULL,
            unavailable_etf_count INTEGER NOT NULL,
            added_count INTEGER NOT NULL,
            removed_count INTEGER NOT NULL,
            changed_count INTEGER NOT NULL,
            details_json TEXT NOT NULL,
            audited_at TEXT NOT NULL
        );
        """
    )


def _components(conn: sqlite3.Connection, day: str, ticker: str) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT component_code,MAX(component_name),SUM(shares_per_cu),SUM(weight)
        FROM etf_pdf_full_component
        WHERE base_date=? AND etf_ticker=? GROUP BY component_code
        """,
        (day,ticker),
    ).fetchall()
    return {
        row[0]:{"name":row[1] or "","shares":row[2],"weight":row[3]}
        for row in rows
    }


def audit_day(conn: sqlite3.Connection, day: str) -> dict[str, Any]:
    initialize(conn)
    universe_count = conn.execute(
        "SELECT COUNT(*) FROM etf_universe_daily WHERE base_date=?", (day,)
    ).fetchone()[0]
    snapshots = conn.execute(
        "SELECT etf_ticker,status FROM etf_pdf_full_snapshot WHERE base_date=?",
        (day,),
    ).fetchall()
    status_by_ticker = {row[0]:row[1] for row in snapshots}
    current_tickers = sorted(
        ticker for ticker,status in status_by_ticker.items() if status == "success"
    )
    unavailable = sorted(
        ticker for ticker,status in status_by_ticker.items() if status != "success"
    )
    compared = 0
    no_prior: list[str] = []
    events: list[tuple[Any, ...]] = []
    counts = {"added":0,"removed":0,"changed":0}

    for ticker in current_tickers:
        previous = conn.execute(
            """
            SELECT MAX(base_date) FROM etf_pdf_full_snapshot
            WHERE etf_ticker=? AND status='success' AND base_date < ?
            """,
            (ticker,day),
        ).fetchone()[0]
        if not previous:
            no_prior.append(ticker)
            continue
        compared += 1
        old, new = _components(conn, previous, ticker), _components(conn, day, ticker)
        for code in sorted(set(old) | set(new)):
            before, after = old.get(code), new.get(code)
            if before is None:
                kind = "added"
            elif after is None:
                kind = "removed"
            else:
                share_changed = abs((after["shares"] or 0) - (before["shares"] or 0)) > 0.000001
                weight_changed = abs((after["weight"] or 0) - (before["weight"] or 0)) > 0.000001
                if not share_changed and not weight_changed:
                    continue
                kind = "changed"
            counts[kind] += 1
            previous_shares = before["shares"] if before else None
            current_shares = after["shares"] if after else None
            previous_weight = before["weight"] if before else None
            current_weight = after["weight"] if after else None
            events.append(
                (
                    day,previous,ticker,code,(after or before)["name"],kind,
                    previous_shares,current_shares,
                    current_shares-previous_shares if before and after else None,
                    previous_weight,current_weight,
                    current_weight-previous_weight if before and after else None,
                )
            )

    fallback = [
        dict(row)
        for row in conn.execute(
            """
            SELECT etf_ticker,source,effective_date,status,component_count
            FROM etf_pdf_issuer_fallback_snapshot WHERE base_date=?
            """,
            (day,),
        )
    ] if conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='etf_pdf_issuer_fallback_snapshot'"
    ).fetchone()[0] else []
    details = {
        "unavailable":unavailable,"no_prior":no_prior,
        "issuer_fallback":fallback,
    }
    now = datetime.now().isoformat(timespec="seconds")
    with conn:
        conn.execute("DELETE FROM etf_pdf_rebalance_event WHERE base_date=?", (day,))
        conn.executemany(
            "INSERT INTO etf_pdf_rebalance_event VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            events,
        )
        conn.execute(
            """
            INSERT INTO etf_pdf_rebalance_audit VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(base_date) DO UPDATE SET
                universe_count=excluded.universe_count,
                compared_etf_count=excluded.compared_etf_count,
                no_prior_etf_count=excluded.no_prior_etf_count,
                unavailable_etf_count=excluded.unavailable_etf_count,
                added_count=excluded.added_count,removed_count=excluded.removed_count,
                changed_count=excluded.changed_count,details_json=excluded.details_json,
                audited_at=excluded.audited_at
            """,
            (
                day,universe_count,compared,len(no_prior),len(unavailable),
                counts["added"],counts["removed"],counts["changed"],
                json.dumps(details,ensure_ascii=False),now,
            ),
        )
    return {
        "base_date":day,"universe_count":universe_count,"compared_etfs":compared,
        "no_prior_etfs":len(no_prior),"unavailable_etfs":len(unavailable),
        **counts,"event_count":len(events),"details":details,"audited_at":now,
    }


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
