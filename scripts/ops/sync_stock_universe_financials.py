#!/usr/bin/env python3
"""
Sync missing financial metrics in stock_universe master table (total_assets, dps, bps, eps, revenue, operating_profit, roe)
from the latest annual records in financial_data.
Uses fast Python in-memory processing.
"""

from __future__ import annotations
import sqlite3
import logging
from pathlib import Path

DB = Path('/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger('sync_stock_universe')


def sync_stock_universe(conn: sqlite3.Connection):
    cur = conn.cursor()

    total_before = cur.execute("SELECT COUNT(*) FROM stock_universe WHERE total_assets IS NULL").fetchone()[0]
    dps_before = cur.execute("SELECT COUNT(*) FROM stock_universe WHERE dps IS NULL").fetchone()[0]
    logger.info(f"Before sync: stock_universe total_assets NULLs: {total_before}, dps NULLs: {dps_before}")

    logger.info("Fetching latest annual financial_data records...")
    fin_records = cur.execute("""
        SELECT f1.stock_code, f1.total_assets, f1.dps, f1.bps, f1.eps, f1.revenue, f1.operating_profit, f1.roe
        FROM financial_data f1
        INNER JOIN (
            SELECT stock_code, MAX(year) AS max_year
            FROM financial_data
            WHERE (is_annual = 1 OR is_annual IS NULL)
            GROUP BY stock_code
        ) f2 ON f1.stock_code = f2.stock_code AND f1.year = f2.max_year
    """).fetchall()

    fin_map = {r[0]: {
        'total_assets': r[1],
        'dps': r[2] if r[2] is not None else 0.0,
        'bps': r[3],
        'eps': r[4],
        'revenue': r[5],
        'operating_profit': r[6],
        'roe': r[7],
    } for r in fin_records}

    logger.info(f"Loaded {len(fin_map)} stock financial records.")

    univ_rows = cur.execute("""
        SELECT stock_code, total_assets, dps, bps, eps, revenue, operating_profit, roe
        FROM stock_universe
    """).fetchall()

    updates = []
    for r in univ_rows:
        code, ast, dps, bps, eps, rev, op, roe = r
        f = fin_map.get(code)
        if not f:
            if dps is None:
                updates.append((ast, 0.0, bps, eps, rev, op, roe, code))
            continue

        new_ast = ast if ast is not None else f['total_assets']
        new_dps = dps if dps is not None else f['dps']
        new_bps = bps if bps is not None else f['bps']
        new_eps = eps if eps is not None else f['eps']
        new_rev = rev if rev is not None else f['revenue']
        new_op = op if op is not None else f['operating_profit']
        new_roe = roe if roe is not None else f['roe']

        if (new_ast != ast or new_dps != dps or new_bps != bps or new_eps != eps or new_rev != rev or new_op != op or new_roe != roe):
            updates.append((new_ast, new_dps, new_bps, new_eps, new_rev, new_op, new_roe, code))

    logger.info(f"Applying batch updates to {len(updates)} stock_universe rows...")
    cur.executemany("""
        UPDATE stock_universe
        SET total_assets = ?,
            dps = ?,
            bps = ?,
            eps = ?,
            revenue = ?,
            operating_profit = ?,
            roe = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE stock_code = ?
    """, updates)

    conn.commit()

    total_after = cur.execute("SELECT COUNT(*) FROM stock_universe WHERE total_assets IS NULL").fetchone()[0]
    dps_after = cur.execute("SELECT COUNT(*) FROM stock_universe WHERE dps IS NULL").fetchone()[0]
    logger.info(f"After sync: stock_universe total_assets NULLs: {total_after}, dps NULLs: {dps_after}")


def main():
    conn = sqlite3.connect(str(DB), timeout=120)
    conn.execute("PRAGMA busy_timeout=120000")
    try:
        sync_stock_universe(conn)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
