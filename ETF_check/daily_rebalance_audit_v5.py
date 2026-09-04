"""Classify proportional basket rescaling separately from quantity rebalancing."""
from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime
from pathlib import Path

import daily_rebalance_audit_v4 as v4
from full_pdf_collector import DB_PATH, connect


MIN_SCALE_COMPONENTS = 5
SCALE_RELATIVE_TOLERANCE = 0.005
SCALE_CONSENSUS_RATIO = 0.80


def initialize(conn) -> None:
    v4.initialize_v4(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS etf_pdf_rebalance_audit_v5 (
            base_date TEXT PRIMARY KEY,
            universe_count INTEGER NOT NULL,
            compared_etf_count INTEGER NOT NULL,
            unavailable_etf_count INTEGER NOT NULL,
            added_count INTEGER NOT NULL,
            removed_count INTEGER NOT NULL,
            quantity_rebalance_count INTEGER NOT NULL,
            basket_rescale_count INTEGER NOT NULL,
            valuation_drift_count INTEGER NOT NULL,
            actionable_event_count INTEGER NOT NULL,
            details_json TEXT NOT NULL,
            audited_at TEXT NOT NULL
        )
        """
    )


def classify_rescaling(conn, day: str) -> int:
    rows = conn.execute(
        """
        SELECT etf_ticker,component_code,previous_shares,current_shares
        FROM etf_pdf_rebalance_event
        WHERE base_date=? AND change_type='shares_changed'
          AND previous_shares > 0 AND current_shares > 0
        ORDER BY etf_ticker,component_code
        """,
        (day,),
    ).fetchall()
    groups: dict[str, list[tuple[str,float]]] = {}
    for ticker,code,before,after in rows:
        groups.setdefault(ticker,[]).append((code,after/before))

    rescaled: list[tuple[str,str]] = []
    for ticker,items in groups.items():
        if len(items) < MIN_SCALE_COMPONENTS:
            continue
        median = statistics.median(ratio for _,ratio in items)
        if median == 0:
            continue
        matches = [
            code for code,ratio in items
            if abs(ratio/median-1) <= SCALE_RELATIVE_TOLERANCE
        ]
        if len(matches) / len(items) >= SCALE_CONSENSUS_RATIO:
            rescaled.extend((ticker,code) for code in matches)
    with conn:
        conn.executemany(
            """
            UPDATE etf_pdf_rebalance_event SET change_type='basket_rescale'
            WHERE base_date=? AND etf_ticker=? AND component_code=?
            """,
            [(day,ticker,code) for ticker,code in rescaled],
        )
    return len(rescaled)


def audit_day(conn, day: str) -> dict:
    initialize(conn)
    base = v4.audit_day(conn,day)
    classify_rescaling(conn,day)
    counts = {
        row[0]:row[1]
        for row in conn.execute(
            """
            SELECT change_type,COUNT(*) FROM etf_pdf_rebalance_event
            WHERE base_date=? GROUP BY change_type
            """,
            (day,),
        )
    }
    actionable = sum(counts.get(kind,0) for kind in ("added","removed","shares_changed"))
    now = datetime.now().isoformat(timespec="seconds")
    values = (
        day,base["universe_count"],base["compared_etfs"],base["unavailable_etfs"],
        counts.get("added",0),counts.get("removed",0),counts.get("shares_changed",0),
        counts.get("basket_rescale",0),counts.get("valuation_drift",0),actionable,
        json.dumps(base["details"],ensure_ascii=False),now,
    )
    with conn:
        conn.execute(
            """
            INSERT INTO etf_pdf_rebalance_audit_v5 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(base_date) DO UPDATE SET
                universe_count=excluded.universe_count,
                compared_etf_count=excluded.compared_etf_count,
                unavailable_etf_count=excluded.unavailable_etf_count,
                added_count=excluded.added_count,removed_count=excluded.removed_count,
                quantity_rebalance_count=excluded.quantity_rebalance_count,
                basket_rescale_count=excluded.basket_rescale_count,
                valuation_drift_count=excluded.valuation_drift_count,
                actionable_event_count=excluded.actionable_event_count,
                details_json=excluded.details_json,audited_at=excluded.audited_at
            """,
            values,
        )
    return {
        "base_date":day,"universe_count":base["universe_count"],
        "compared_etfs":base["compared_etfs"],
        "unavailable_etfs":base["unavailable_etfs"],
        "added":counts.get("added",0),"removed":counts.get("removed",0),
        "quantity_rebalance":counts.get("shares_changed",0),
        "basket_rescale":counts.get("basket_rescale",0),
        "valuation_drift":counts.get("valuation_drift",0),
        "actionable_events":actionable,"details":base["details"],"audited_at":now,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date")
    parser.add_argument("--db",default=str(DB_PATH))
    args = parser.parse_args()
    conn = connect(Path(args.db))
    day = args.date or conn.execute(
        "SELECT MAX(base_date) FROM etf_pdf_full_snapshot"
    ).fetchone()[0]
    if not day:
        raise RuntimeError("No ETF PDF snapshot is available")
    print(json.dumps(audit_day(conn,day),ensure_ascii=False,indent=2))
    conn.close()


if __name__ == "__main__":
    main()
