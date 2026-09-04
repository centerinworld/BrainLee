"""Fast daily ETF audit separating structural rebalances from valuation drift."""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from daily_rebalance_audit import initialize
from daily_rebalance_audit_v2 import sync_fallback_compatibility
from full_pdf_collector import DB_PATH, connect


def initialize_v4(conn: sqlite3.Connection) -> None:
    initialize(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS etf_pdf_rebalance_audit_v4 (
            base_date TEXT PRIMARY KEY,
            universe_count INTEGER NOT NULL,
            compared_etf_count INTEGER NOT NULL,
            no_prior_etf_count INTEGER NOT NULL,
            unavailable_etf_count INTEGER NOT NULL,
            added_count INTEGER NOT NULL,
            removed_count INTEGER NOT NULL,
            shares_changed_count INTEGER NOT NULL,
            valuation_drift_count INTEGER NOT NULL,
            structural_event_count INTEGER NOT NULL,
            details_json TEXT NOT NULL,
            audited_at TEXT NOT NULL
        )
        """
    )


def audit_day(conn: sqlite3.Connection, day: str) -> dict:
    initialize_v4(conn)
    sync_fallback_compatibility(conn, day)
    universe_count = conn.execute(
        "SELECT COUNT(*) FROM etf_universe_daily WHERE base_date=?", (day,)
    ).fetchone()[0]
    statuses = conn.execute(
        "SELECT etf_ticker,status FROM etf_pdf_full_snapshot WHERE base_date=?",
        (day,),
    ).fetchall()
    unavailable = sorted(row[0] for row in statuses if row[1] != "success")

    with conn:
        conn.execute("DELETE FROM etf_pdf_rebalance_event WHERE base_date=?", (day,))
        conn.execute(
            """
            INSERT INTO etf_pdf_rebalance_event(
                base_date,previous_date,etf_ticker,component_code,component_name,
                change_type,previous_shares,current_shares,share_change,
                previous_weight,current_weight,weight_change
            )
            WITH current_etfs AS (
                SELECT etf_ticker
                FROM etf_pdf_full_snapshot
                WHERE base_date=? AND status='success'
            ), previous_dates AS (
                SELECT c.etf_ticker,MAX(p.base_date) previous_date
                FROM current_etfs c
                LEFT JOIN etf_pdf_full_snapshot p
                  ON p.etf_ticker=c.etf_ticker
                 AND p.status='success' AND p.base_date < ?
                GROUP BY c.etf_ticker
            ), old AS (
                SELECT d.etf_ticker,d.previous_date,x.component_code,
                       MAX(x.component_name) component_name,
                       SUM(x.shares_per_cu) shares,SUM(x.weight) weight
                FROM previous_dates d
                JOIN etf_pdf_full_component x
                  ON x.etf_ticker=d.etf_ticker AND x.base_date=d.previous_date
                GROUP BY d.etf_ticker,d.previous_date,x.component_code
            ), new AS (
                SELECT x.etf_ticker,x.component_code,MAX(x.component_name) component_name,
                       SUM(x.shares_per_cu) shares,SUM(x.weight) weight
                FROM etf_pdf_full_component x
                JOIN current_etfs c USING(etf_ticker)
                WHERE x.base_date=?
                GROUP BY x.etf_ticker,x.component_code
            ), keys AS (
                SELECT etf_ticker,component_code FROM old
                UNION
                SELECT etf_ticker,component_code FROM new
            ), diff AS (
                SELECT k.etf_ticker,k.component_code,
                       COALESCE(o.previous_date,p.previous_date) previous_date,
                       COALESCE(n.component_name,o.component_name,'') component_name,
                       o.shares previous_shares,n.shares current_shares,
                       o.weight previous_weight,n.weight current_weight,
                       CASE
                         WHEN o.component_code IS NULL THEN 'added'
                         WHEN n.component_code IS NULL THEN 'removed'
                         WHEN (o.shares IS NULL) != (n.shares IS NULL)
                           OR ABS(COALESCE(n.shares,0)-COALESCE(o.shares,0)) > 0.000001
                           THEN 'shares_changed'
                         WHEN (o.weight IS NULL) != (n.weight IS NULL)
                           OR ABS(COALESCE(n.weight,0)-COALESCE(o.weight,0)) > 0.000001
                           THEN 'valuation_drift'
                       END change_type
                FROM keys k
                JOIN previous_dates p USING(etf_ticker)
                LEFT JOIN old o USING(etf_ticker,component_code)
                LEFT JOIN new n USING(etf_ticker,component_code)
                WHERE p.previous_date IS NOT NULL
            )
            SELECT ?,previous_date,etf_ticker,component_code,component_name,change_type,
                   previous_shares,current_shares,
                   CASE WHEN previous_shares IS NOT NULL AND current_shares IS NOT NULL
                        THEN current_shares-previous_shares END,
                   previous_weight,current_weight,
                   CASE WHEN previous_weight IS NOT NULL AND current_weight IS NOT NULL
                        THEN current_weight-previous_weight END
            FROM diff WHERE change_type IS NOT NULL
            """,
            (day,day,day,day),
        )

    compared = conn.execute(
        """
        SELECT COUNT(*) FROM etf_pdf_full_snapshot c
        WHERE c.base_date=? AND c.status='success' AND EXISTS(
            SELECT 1 FROM etf_pdf_full_snapshot p
            WHERE p.etf_ticker=c.etf_ticker AND p.status='success' AND p.base_date < c.base_date
        )
        """,
        (day,),
    ).fetchone()[0]
    successful = sum(1 for row in statuses if row[1] == "success")
    no_prior = successful - compared
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
    fallback = [
        dict(row)
        for row in conn.execute(
            """
            SELECT etf_ticker,source,effective_date,status,component_count
            FROM etf_pdf_issuer_fallback_snapshot WHERE base_date=?
            """,
            (day,),
        )
    ]
    structural = sum(counts.get(kind,0) for kind in ("added","removed","shares_changed"))
    details = {"unavailable":unavailable,"issuer_fallback":fallback}
    now = datetime.now().isoformat(timespec="seconds")
    values = (
        day,universe_count,compared,no_prior,len(unavailable),
        counts.get("added",0),counts.get("removed",0),counts.get("shares_changed",0),
        counts.get("valuation_drift",0),structural,json.dumps(details,ensure_ascii=False),now,
    )
    with conn:
        conn.execute(
            """
            INSERT INTO etf_pdf_rebalance_audit_v4 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(base_date) DO UPDATE SET
                universe_count=excluded.universe_count,
                compared_etf_count=excluded.compared_etf_count,
                no_prior_etf_count=excluded.no_prior_etf_count,
                unavailable_etf_count=excluded.unavailable_etf_count,
                added_count=excluded.added_count,removed_count=excluded.removed_count,
                shares_changed_count=excluded.shares_changed_count,
                valuation_drift_count=excluded.valuation_drift_count,
                structural_event_count=excluded.structural_event_count,
                details_json=excluded.details_json,audited_at=excluded.audited_at
            """,
            values,
        )
    return {
        "base_date":day,"universe_count":universe_count,"compared_etfs":compared,
        "no_prior_etfs":no_prior,"unavailable_etfs":len(unavailable),
        "added":counts.get("added",0),"removed":counts.get("removed",0),
        "shares_changed":counts.get("shares_changed",0),
        "valuation_drift":counts.get("valuation_drift",0),
        "structural_events":structural,"details":details,"audited_at":now,
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
