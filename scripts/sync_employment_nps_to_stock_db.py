#!/usr/bin/env python3
"""Sync local employment DB NPS monthly changes into stock.db.

The public NPS API can be flaky, but employment_monitor/employment.db already
contains monthly NPS new-hire / termination aggregates. This script mirrors those
aggregates into stock.db.nps_workplace_monthly so strategy code can consume a
single stock.db source.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from db_compat import connect_primary_db


ROOT = Path(__file__).resolve().parents[1]
STOCK_DB = ROOT / "stock.db"
EMP_DB = ROOT / "employment_monitor" / "employment.db"


def sync(limit: int = 0) -> dict[str, int | str]:
    # employment.db is intentionally an independent SQLite store.  The primary
    # stock database can be PostgreSQL, where SQLite ATTACH is unavailable.
    source = sqlite3.connect(EMP_DB)
    source.row_factory = sqlite3.Row
    query = """
        SELECT
            data_ym AS ym, stock_code, new_hires, terminations,
            net_change, wkpl_count, fetched_at
        FROM nps_monthly
        WHERE data_ym IS NOT NULL AND stock_code IS NOT NULL
        ORDER BY data_ym, stock_code
    """
    if limit > 0:
        query += f" LIMIT {int(limit)}"
    rows = source.execute(query).fetchall()
    source.close()

    stock = connect_primary_db()
    try:
        names = {
            row[0]: row[1]
            for row in stock.execute(
                "SELECT stock_code, stock_name FROM stock_universe"
            ).fetchall()
        }
        inserted = 0
        for r in rows:
            stock_name = names.get(r["stock_code"], r["stock_code"])
            raw = {
                "source": "employment_db.nps_monthly",
                "net_change": r["net_change"],
                "wkpl_count": r["wkpl_count"],
                "source_fetched_at": r["fetched_at"],
                "synced_at": datetime.now().isoformat(timespec="seconds"),
            }
            stock.execute(
                """
                INSERT OR REPLACE INTO nps_workplace_monthly
                (ym, stock_code, stock_name, seq, wkpl_nm, bzowr_rgst_no,
                 nw_acqzr_cnt, lss_jnngp_cnt, raw_base_json, fetched_at)
                VALUES (?, ?, ?, NULL, ?, NULL, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    r["ym"], r["stock_code"], stock_name, stock_name,
                    int(r["new_hires"] or 0), int(r["terminations"] or 0),
                    json.dumps(raw, ensure_ascii=False),
                ),
            )
            inserted += 1

        stock.commit()
        total = stock.execute("SELECT COUNT(*) FROM nps_workplace_monthly").fetchone()[0]
        stocks = stock.execute("SELECT COUNT(DISTINCT stock_code) FROM nps_workplace_monthly").fetchone()[0]
        min_ym, max_ym = stock.execute("SELECT MIN(ym), MAX(ym) FROM nps_workplace_monthly").fetchone()
    finally:
        stock.close()
    return {
        "source_rows": len(rows),
        "upserted": inserted,
        "target_total_rows": total,
        "target_stocks": stocks,
        "min_ym": min_ym or "",
        "max_ym": max_ym or "",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="sync only N rows for testing")
    args = parser.parse_args()
    result = sync(limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
