#!/usr/bin/env python3
"""
Backfill raw-material columns in cost_structure from annual DART material purchases.

The annual purchase table is a reliable lower-frequency source.  Store it on the
Q4 cost_structure row and let signal discovery forward-fill it after disclosure.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime")
DB_PATH = ROOT / "stock.db"


def main() -> None:
    con = sqlite3.connect(DB_PATH, timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=60000")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS cost_structure (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            year INTEGER NOT NULL,
            quarter INTEGER NOT NULL,
            raw_material_cost REAL,
            labor_cost REAL,
            overhead_cost REAL,
            total_cogs REAL,
            revenue REAL,
            raw_material_ratio REAL,
            cogs_ratio REAL,
            yoy_raw_material_chg REAL,
            data_source TEXT DEFAULT 'dart_cost',
            collected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(stock_code, year, quarter)
        )
        """
    )

    rows = con.execute(
        """
        WITH annual AS (
          SELECT
            dmp.stock_code,
            COALESCE(su.stock_name, sm.stock_name) AS stock_name,
            dmp.year,
            dmp.material_purchase_krw AS raw_material_cost,
            fd.revenue,
            LAG(dmp.material_purchase_krw) OVER (
              PARTITION BY dmp.stock_code ORDER BY dmp.year
            ) AS prev_raw_material_cost
          FROM dart_material_purchase dmp
          LEFT JOIN (
            SELECT stock_code, year,
                   MAX(CASE WHEN report_type='CFS' THEN revenue ELSE 0 END) as cfs_rev,
                   MAX(revenue) as max_rev,
                   MAX(CASE WHEN report_type='CFS' THEN revenue ELSE 0 END) revenue
            FROM financial_data
            WHERE is_annual = 1 AND quarter = 0 AND revenue > 1000000000
            GROUP BY stock_code, year
          ) fd
            ON fd.stock_code = dmp.stock_code
           AND fd.year = dmp.year
          LEFT JOIN stock_universe su ON su.stock_code = dmp.stock_code
          LEFT JOIN stock_meta sm ON sm.stock_code = dmp.stock_code
          WHERE dmp.material_purchase_krw IS NOT NULL
            AND dmp.material_purchase_krw >= 10000000
        )
        SELECT
          stock_code,
          stock_name,
          year,
          raw_material_cost,
          revenue,
          CASE
            WHEN revenue > 0 THEN raw_material_cost / revenue
            ELSE NULL
          END AS raw_material_ratio,
          CASE
            WHEN prev_raw_material_cost IS NOT NULL AND prev_raw_material_cost != 0
            THEN raw_material_cost / ABS(prev_raw_material_cost) - 1
            ELSE NULL
          END AS yoy_raw_material_chg
        FROM annual
        """
    ).fetchall()

    updated = 0
    for r in rows:
        con.execute(
            """
            INSERT INTO cost_structure(
              stock_code, stock_name, year, quarter,
              raw_material_cost, revenue, raw_material_ratio, yoy_raw_material_chg,
              data_source, collected_at
            ) VALUES(?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(stock_code, year, quarter) DO UPDATE SET
              stock_name=COALESCE(excluded.stock_name, cost_structure.stock_name),
              raw_material_cost=COALESCE(excluded.raw_material_cost, cost_structure.raw_material_cost),
              revenue=COALESCE(cost_structure.revenue, excluded.revenue),
              raw_material_ratio=COALESCE(excluded.raw_material_ratio, cost_structure.raw_material_ratio),
              yoy_raw_material_chg=COALESCE(excluded.yoy_raw_material_chg, cost_structure.yoy_raw_material_chg),
              data_source=CASE
                WHEN cost_structure.data_source IS NULL THEN 'dart_material_annual'
                WHEN instr(cost_structure.data_source, 'dart_material_annual') = 0
                  THEN cost_structure.data_source || '+dart_material_annual'
                ELSE cost_structure.data_source
              END,
              collected_at=CURRENT_TIMESTAMP
            """,
            (
                r["stock_code"],
                r["stock_name"],
                r["year"],
                4,
                r["raw_material_cost"],
                r["revenue"],
                r["raw_material_ratio"],
                r["yoy_raw_material_chg"],
                "dart_material_annual",
            ),
        )
        updated += 1

    con.commit()
    sample = con.execute(
        """
        SELECT stock_code, stock_name, year, quarter, raw_material_cost,
               revenue, raw_material_ratio, yoy_raw_material_chg, data_source
        FROM cost_structure
        WHERE stock_code = '200470'
        ORDER BY year, quarter
        """
    ).fetchall()
    con.close()

    print({"rows_backfilled": updated, "sample_stock": "200470"})
    for r in sample:
        print(dict(r))


if __name__ == "__main__":
    main()
