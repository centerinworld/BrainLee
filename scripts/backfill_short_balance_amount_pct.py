#!/usr/bin/env python3
"""
Backfill short_sell_daily.borrow_bal_amt and borrow_bal_pct.

PublicData V2 item status currently provides daily borrow balance quantity but
not amount/ratio. The companion short_rank_daily table provides balance amount
(lnb_bal), and stock_universe provides listed shares for a stable derived ratio.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_utils import connect_stock_db


def _count(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        """
        SELECT COUNT(*) AS rows,
               SUM(CASE WHEN borrow_bal_qty IS NOT NULL AND borrow_bal_qty > 0 THEN 1 ELSE 0 END) AS qty_ok,
               SUM(CASE WHEN borrow_bal_amt IS NOT NULL AND borrow_bal_amt > 0 THEN 1 ELSE 0 END) AS amt_ok,
               SUM(CASE WHEN borrow_bal_pct IS NOT NULL AND borrow_bal_pct > 0 THEN 1 ELSE 0 END) AS pct_ok
        FROM short_sell_daily
        """
    ).fetchone()
    return {
        "rows": int(row[0] or 0),
        "qty_ok": int(row[1] or 0),
        "amt_ok": int(row[2] or 0),
        "pct_ok": int(row[3] or 0),
    }


def backfill(start: str | None = None, end: str | None = None) -> dict:
    conn = connect_stock_db(timeout=180)
    conn.execute("PRAGMA busy_timeout=180000")
    try:
        before = _count(conn)

        where = []
        params: list[str] = []
        if start:
            where.append("d.bas_dt >= ?")
            params.append(start)
        if end:
            where.append("d.bas_dt <= ?")
            params.append(end)
        date_filter = (" AND " + " AND ".join(where)) if where else ""

        conn.execute("DROP TABLE IF EXISTS temp_latest_shares")
        conn.execute(
            """
            CREATE TEMP TABLE temp_latest_shares AS
            WITH all_shares AS (
                SELECT stock_code, shares_issued, snapshot_date AS asof_date, 1 AS src_priority
                FROM krx_security_share_snapshot
                WHERE shares_issued IS NOT NULL AND shares_issued > 0
                UNION ALL
                SELECT stock_code, shares_issued, snapshot_date AS asof_date, 2 AS src_priority
                FROM stock_base_info_history
                WHERE shares_issued IS NOT NULL AND shares_issued > 0
                UNION ALL
                SELECT stock_code, shares_issued, base_date AS asof_date, 3 AS src_priority
                FROM stock_universe
                WHERE shares_issued IS NOT NULL AND shares_issued > 0
            ),
            ranked AS (
                SELECT stock_code, shares_issued,
                       ROW_NUMBER() OVER (
                           PARTITION BY stock_code
                           ORDER BY asof_date DESC, src_priority ASC
                       ) AS rn
                FROM all_shares
            )
            SELECT stock_code, shares_issued
            FROM ranked
            WHERE rn = 1
            """
        )
        conn.execute("CREATE INDEX temp_idx_latest_shares_code ON temp_latest_shares(stock_code)")

        conn.execute("DROP TABLE IF EXISTS temp_short_rank_for_backfill")
        conn.execute(
            """
            CREATE TEMP TABLE temp_short_rank_for_backfill AS
            SELECT bas_dt, stock_code,
                   MAX(COALESCE(lnb_rman_stck_cnt, 0)) AS rank_qty,
                   MAX(COALESCE(lnb_bal, 0)) AS rank_amt
            FROM short_rank_daily
            WHERE stock_code IS NOT NULL
              AND stock_code != ''
            GROUP BY bas_dt, stock_code
            """
        )
        conn.execute("CREATE INDEX temp_idx_rank_date_code ON temp_short_rank_for_backfill(bas_dt, stock_code)")

        # Fill missing quantity first for rows where item-status quantity is absent
        # but rank table has a matching balance.
        conn.execute(
            f"""
            UPDATE short_sell_daily AS d
            SET borrow_bal_qty = (
                SELECT r.rank_qty
                FROM temp_short_rank_for_backfill r
                WHERE r.bas_dt = d.bas_dt
                  AND r.stock_code = d.stock_code
                  AND r.rank_qty > 0
            )
            WHERE (d.borrow_bal_qty IS NULL OR d.borrow_bal_qty <= 0)
              {date_filter}
              AND EXISTS (
                SELECT 1 FROM temp_short_rank_for_backfill r
                WHERE r.bas_dt = d.bas_dt
                  AND r.stock_code = d.stock_code
                  AND r.rank_qty > 0
              )
            """,
            params,
        )
        qty_updates = conn.total_changes

        conn.execute(
            f"""
            UPDATE short_sell_daily AS d
            SET borrow_bal_amt = COALESCE((
                    SELECT r.rank_amt
                    FROM temp_short_rank_for_backfill r
                    WHERE r.bas_dt = d.bas_dt
                      AND r.stock_code = d.stock_code
                      AND r.rank_amt > 0
                ), d.borrow_bal_amt),
                borrow_bal_pct = COALESCE((
                    SELECT ROUND(d.borrow_bal_qty * 100.0 / s.shares_issued, 6)
                    FROM temp_latest_shares s
                    WHERE s.stock_code = d.stock_code
                      AND s.shares_issued > 0
                      AND d.borrow_bal_qty IS NOT NULL
                      AND d.borrow_bal_qty > 0
                ), d.borrow_bal_pct)
            WHERE (
                    d.borrow_bal_amt IS NULL OR d.borrow_bal_amt <= 0
                 OR d.borrow_bal_pct IS NULL OR d.borrow_bal_pct <= 0
              )
              {date_filter}
              AND (
                  EXISTS (
                    SELECT 1 FROM temp_short_rank_for_backfill r
                    WHERE r.bas_dt = d.bas_dt
                      AND r.stock_code = d.stock_code
                      AND r.rank_amt > 0
                  )
                  OR EXISTS (
                    SELECT 1 FROM temp_latest_shares s
                    WHERE s.stock_code = d.stock_code
                      AND s.shares_issued > 0
                      AND d.borrow_bal_qty IS NOT NULL
                      AND d.borrow_bal_qty > 0
                  )
              )
            """,
            params,
        )
        total_updates = conn.total_changes
        conn.commit()

        after = _count(conn)
        return {
            "start": start,
            "end": end,
            "before": before,
            "after": after,
            "qty_rows_updated_or_touched": int(qty_updates),
            "total_rows_updated_or_touched": int(total_updates),
            "amt_added": after["amt_ok"] - before["amt_ok"],
            "pct_added": after["pct_ok"] - before["pct_ok"],
        }
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill short balance amount and ratio")
    ap.add_argument("--start", default=None, help="YYYYMMDD")
    ap.add_argument("--end", default=None, help="YYYYMMDD")
    args = ap.parse_args()
    print(json.dumps(backfill(args.start, args.end), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
