#!/usr/bin/env python3
"""Remove inventory/cost records for fiscal periods not yet publicly reportable."""

from __future__ import annotations

import argparse
import sqlite3
from datetime import date

from db_compat import connect_primary_db


def is_publicly_available(year: int, quarter: int, as_of: date) -> bool:
    earliest_month = {1: 5, 2: 8, 3: 11, 4: 3}.get(quarter)
    if earliest_month is None:
        return False
    return as_of >= date(year + (quarter == 4), earliest_month, 1)


def table_exists(conn, table: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {table} LIMIT 1")
        return True
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Actually delete invalid rows")
    args = parser.parse_args()
    as_of = date.today()
    conn = connect_primary_db(timeout=60, row_factory=sqlite3.Row)
    try:
        invalid = [
            (row["stock_code"], int(row["fiscal_year"]), int(row["fiscal_quarter"]), row["report_type"])
            for row in conn.execute(
                "SELECT stock_code, fiscal_year, fiscal_quarter, report_type FROM dart_cost_quarterly"
            ).fetchall()
            if not is_publicly_available(int(row["fiscal_year"]), int(row["fiscal_quarter"]), as_of)
        ]
        print(f"as_of={as_of.isoformat()} invalid_cost_rows={len(invalid)}")
        if not args.apply or not invalid:
            return

        removed = {"dart_cost_quarterly": 0, "dart_tenbagger_triggers_quarterly": 0, "inventory_sales_signals": 0}
        for stock_code, year, quarter, report_type in invalid:
            cur = conn.execute(
                "DELETE FROM dart_cost_quarterly WHERE stock_code=? AND fiscal_year=? AND fiscal_quarter=? AND report_type=?",
                (stock_code, year, quarter, report_type),
            )
            removed["dart_cost_quarterly"] += cur.rowcount
            if table_exists(conn, "dart_tenbagger_triggers_quarterly"):
                cur = conn.execute(
                    """DELETE FROM dart_tenbagger_triggers_quarterly
                       WHERE stock_code=? AND fiscal_year=? AND fiscal_quarter=? AND report_type=?
                         AND metric_name IN ('inventory_assets', 'material_cost', 'depreciation')""",
                    (stock_code, year, quarter, report_type),
                )
                removed["dart_tenbagger_triggers_quarterly"] += cur.rowcount
            if table_exists(conn, "inventory_sales_signals"):
                cur = conn.execute(
                    "DELETE FROM inventory_sales_signals WHERE stock_code=? AND fiscal_year=? AND fiscal_quarter=? AND fs_div=?",
                    (stock_code, year, quarter, report_type),
                )
                removed["inventory_sales_signals"] += cur.rowcount
        conn.commit()
        print("removed=" + ", ".join(f"{table}:{count}" for table, count in removed.items()))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
