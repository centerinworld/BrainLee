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


ROOT = Path(__file__).resolve().parents[1]
STOCK_DB = ROOT / "stock.db"
EMP_DB = ROOT / "employment_monitor" / "employment.db"


def sync(limit: int = 0) -> dict[str, int | str]:
    stock = sqlite3.connect(STOCK_DB, timeout=60)
    stock.row_factory = sqlite3.Row
    stock.execute("PRAGMA busy_timeout=60000")
    stock.execute(f"ATTACH DATABASE '{EMP_DB}' AS empdb")

    query = """
        SELECT
            n.data_ym AS ym,
            n.stock_code,
            COALESCE(su.stock_name, n.stock_code) AS stock_name,
            n.new_hires,
            n.terminations,
            n.net_change,
            n.wkpl_count,
            n.fetched_at
        FROM empdb.nps_monthly n
        LEFT JOIN stock_universe su ON su.stock_code = n.stock_code
        WHERE n.data_ym IS NOT NULL
          AND n.stock_code IS NOT NULL
        ORDER BY n.data_ym, n.stock_code
    """
    if limit > 0:
        query += f" LIMIT {int(limit)}"

    rows = stock.execute(query).fetchall()
    inserted = 0
    for r in rows:
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
                r["ym"],
                r["stock_code"],
                r["stock_name"],
                r["stock_name"],
                int(r["new_hires"] or 0),
                int(r["terminations"] or 0),
                json.dumps(raw, ensure_ascii=False),
            ),
        )
        inserted += 1

    stock.commit()
    total = stock.execute("SELECT COUNT(*) FROM nps_workplace_monthly").fetchone()[0]
    stocks = stock.execute("SELECT COUNT(DISTINCT stock_code) FROM nps_workplace_monthly").fetchone()[0]
    min_ym, max_ym = stock.execute("SELECT MIN(ym), MAX(ym) FROM nps_workplace_monthly").fetchone()
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
