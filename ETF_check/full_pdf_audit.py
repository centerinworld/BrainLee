"""Validate and diff complete KRX ETF PDF publications."""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from full_pdf_collector import DB_PATH, connect


AUDIT_ROOT = Path(__file__).with_name("audits")


def initialize(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS etf_pdf_full_change (
            base_date TEXT NOT NULL,
            previous_date TEXT NOT NULL,
            etf_ticker TEXT NOT NULL,
            component_code TEXT NOT NULL,
            component_name TEXT NOT NULL,
            change_type TEXT NOT NULL,
            previous_shares REAL,
            current_shares REAL,
            share_change REAL,
            previous_weight REAL,
            current_weight REAL,
            weight_change REAL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(base_date, etf_ticker, component_code, change_type)
        );
        CREATE INDEX IF NOT EXISTS idx_pdf_full_change_component
            ON etf_pdf_full_change(base_date, component_code);
        """
    )


def publication_dates(conn: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in conn.execute(
            "SELECT base_date FROM etf_pdf_full_publication ORDER BY base_date DESC LIMIT 2"
        )
    ]


def health(conn: sqlite3.Connection, day: str | None = None) -> dict[str, Any]:
    selected = day or conn.execute(
        "SELECT MAX(base_date) FROM etf_pdf_full_snapshot"
    ).fetchone()[0]
    if not selected:
        return {"base_date":None,"status":"no_snapshot"}
    publication = conn.execute(
        "SELECT * FROM etf_pdf_full_publication WHERE base_date=?",(selected,)
    ).fetchone()
    row = conn.execute(
        """
        SELECT COUNT(*) snapshots,
               SUM(status='success') successes,
               SUM(status='empty') empty_count,
               SUM(status='error') error_count,
               COALESCE(SUM(component_count),0) component_count,
               SUM(raw_path IS NULL OR raw_path='') missing_raw,
               SUM(raw_sha256 IS NULL OR LENGTH(raw_sha256)!=64) invalid_hash,
               SUM(CASE WHEN status='success' AND component_count<=0 THEN 1 ELSE 0 END) invalid_success
        FROM etf_pdf_full_snapshot WHERE base_date=?
        """,
        (selected,),
    ).fetchone()
    duplicate_rows = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT etf_ticker,component_order,COUNT(*) n
            FROM etf_pdf_full_component WHERE base_date=?
            GROUP BY etf_ticker,component_order HAVING n>1
        )
        """,
        (selected,),
    ).fetchone()[0]
    raw_missing_on_disk = 0
    for raw in conn.execute(
        "SELECT raw_path FROM etf_pdf_full_snapshot WHERE base_date=? AND status='success'",
        (selected,),
    ):
        if not raw[0] or not Path(raw[0]).exists():
            raw_missing_on_disk += 1
    published = publication is not None
    checks_ok = bool(
        published
        and int(row["snapshots"] or 0) == int(publication["universe_count"])
        and not int(row["empty_count"] or 0)
        and not int(row["error_count"] or 0)
        and not int(row["missing_raw"] or 0)
        and not int(row["invalid_hash"] or 0)
        and not int(row["invalid_success"] or 0)
        and not duplicate_rows
        and not raw_missing_on_disk
    )
    return {
        "base_date":selected,"status":"healthy" if checks_ok else "incomplete_or_invalid",
        "published":published,"snapshots":int(row["snapshots"] or 0),
        "successes":int(row["successes"] or 0),"empty":int(row["empty_count"] or 0),
        "errors":int(row["error_count"] or 0),"components":int(row["component_count"] or 0),
        "missing_raw_metadata":int(row["missing_raw"] or 0),
        "invalid_hash":int(row["invalid_hash"] or 0),
        "invalid_success":int(row["invalid_success"] or 0),
        "duplicate_rows":int(duplicate_rows),"raw_missing_on_disk":raw_missing_on_disk,
    }


def build_changes(conn: sqlite3.Connection, current: str, previous: str) -> int:
    initialize(conn)
    with conn:
        conn.execute("DELETE FROM etf_pdf_full_change WHERE base_date=?",(current,))
        conn.execute(
            """
            INSERT INTO etf_pdf_full_change(
                base_date,previous_date,etf_ticker,component_code,component_name,
                change_type,previous_shares,current_shares,share_change,
                previous_weight,current_weight,weight_change
            )
            WITH keys AS (
                SELECT etf_ticker,component_code FROM etf_pdf_full_component WHERE base_date=?
                UNION
                SELECT etf_ticker,component_code FROM etf_pdf_full_component WHERE base_date=?
            ), old AS (
                SELECT etf_ticker,component_code,MAX(component_name) component_name,
                       SUM(shares_per_cu) shares,SUM(weight) weight
                FROM etf_pdf_full_component WHERE base_date=? GROUP BY 1,2
            ), new AS (
                SELECT etf_ticker,component_code,MAX(component_name) component_name,
                       SUM(shares_per_cu) shares,SUM(weight) weight
                FROM etf_pdf_full_component WHERE base_date=? GROUP BY 1,2
            )
            SELECT ?,?,k.etf_ticker,k.component_code,
                   COALESCE(new.component_name,old.component_name,''),
                   CASE WHEN old.component_code IS NULL THEN 'added'
                        WHEN new.component_code IS NULL THEN 'removed'
                        WHEN ABS(COALESCE(new.shares,0)-COALESCE(old.shares,0))>0.000001
                          OR ABS(COALESCE(new.weight,0)-COALESCE(old.weight,0))>0.000001
                        THEN 'changed' ELSE 'unchanged' END,
                   old.shares,new.shares,
                   CASE WHEN old.shares IS NOT NULL AND new.shares IS NOT NULL
                        THEN new.shares-old.shares END,
                   old.weight,new.weight,
                   CASE WHEN old.weight IS NOT NULL AND new.weight IS NOT NULL
                        THEN new.weight-old.weight END
            FROM keys k
            LEFT JOIN old USING(etf_ticker,component_code)
            LEFT JOIN new USING(etf_ticker,component_code)
            WHERE old.component_code IS NULL OR new.component_code IS NULL
               OR ABS(COALESCE(new.shares,0)-COALESCE(old.shares,0))>0.000001
               OR ABS(COALESCE(new.weight,0)-COALESCE(old.weight,0))>0.000001
            """,
            (current,previous,previous,current,current,previous),
        )
    return conn.execute(
        "SELECT COUNT(*) FROM etf_pdf_full_change WHERE base_date=?",(current,)
    ).fetchone()[0]


def audit(db_path: Path = DB_PATH, audit_root: Path = AUDIT_ROOT) -> dict[str, Any]:
    conn=connect(db_path); initialize(conn)
    dates=publication_dates(conn)
    latest_snapshot=conn.execute("SELECT MAX(base_date) FROM etf_pdf_full_snapshot").fetchone()[0]
    report=health(conn,latest_snapshot)
    report["publication_dates"]=dates
    if len(dates)>=2 and report["status"]=="healthy" and report["base_date"]==dates[0]:
        report["changes"]=build_changes(conn,dates[0],dates[1])
        report["previous_date"]=dates[1]
        report["change_summary"]={
            row[0]:row[1] for row in conn.execute(
                """
                SELECT change_type,COUNT(*) FROM etf_pdf_full_change
                WHERE base_date=? GROUP BY change_type
                """,(dates[0],)
            )
        }
    else:
        report["changes"]=None
        report["reason"]="Two healthy complete publications are required for a diff"
    report["audited_at"]=datetime.now().isoformat(timespec="seconds")
    audit_root.mkdir(parents=True,exist_ok=True)
    target=audit_root/f"{report['base_date'] or 'no_snapshot'}.json"
    temporary=target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    temporary.replace(target)
    conn.close()
    return report


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--db",default=str(DB_PATH))
    parser.add_argument("--audit-root",default=str(AUDIT_ROOT))
    args=parser.parse_args()
    print(json.dumps(audit(Path(args.db),Path(args.audit_root)),ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
