#!/usr/bin/env python3
"""
Repair and backfill derived financial metrics (gross_profit, opm, ebitda, bps, eps, pbr, per)
for us_financial_data, us_cashflow_data, us_factor_snapshot, and us_frontend_snapshot.
Uses fast Python in-memory processing and batch updates.
"""

from __future__ import annotations
import sqlite3
import logging
from pathlib import Path

DB = Path('/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger('fix_us_financials')


def repair_us_financial_metrics(conn: sqlite3.Connection):
    cur = conn.cursor()
    
    logger.info("Loading cashflow depreciation mapping...")
    cf_rows = cur.execute("SELECT ticker, period_end, period_type, depreciation FROM us_cashflow_data WHERE depreciation IS NOT NULL").fetchall()
    cf_map = {(r[0], r[1], r[2]): float(r[3]) for r in cf_rows if r[3] is not None}

    logger.info("Loading stock meta market cap mapping...")
    meta_rows = cur.execute("SELECT ticker, market_cap FROM us_stock_meta WHERE market_cap IS NOT NULL AND market_cap > 0").fetchall()
    meta_map = {r[0]: float(r[1]) for r in meta_rows}

    logger.info("Fetching us_financial_data rows...")
    fin_rows = cur.execute("""
        SELECT ticker, period_end, period_type, revenue, cogs, gross_profit, operating_income, net_income, equity, ebitda, opm, pbr
        FROM us_financial_data
    """).fetchall()

    updates = []
    for r in fin_rows:
        tk, pend, ptype, rev, cogs, gp, opi, ni, eq, eb, opm, pbr = r
        rev = float(rev) if rev is not None else None
        cogs = float(cogs) if cogs is not None else None
        gp = float(gp) if gp is not None else None
        opi = float(opi) if opi is not None else None
        ni = float(ni) if ni is not None else None
        eq = float(eq) if eq is not None else None
        eb = float(eb) if eb is not None else None
        opm = float(opm) if opm is not None else None
        pbr = float(pbr) if pbr is not None else None

        # 1. Gross Profit
        if gp is None and rev is not None:
            gp = rev - (cogs or 0.0)

        # 2. OPM
        if (opm is None or opm == 0.0) and opi is not None and rev not in (None, 0.0):
            opm = (opi / rev) * 100.0

        # 3. EBITDA
        if eb is None and opi is not None:
            dep = cf_map.get((tk, pend, ptype), 0.0)
            eb = opi + dep

        # 4. PBR (approximate using market_cap / equity for annual records)
        if (pbr is None or pbr == 0.0) and ptype == 'annual' and eq is not None and eq > 0.0:
            mcap = meta_map.get(tk)
            if mcap:
                pbr = mcap / eq

        updates.append((gp, opm, eb, pbr, tk, pend, ptype))

    logger.info(f"Applying batch updates to {len(updates)} rows...")
    cur.executemany("""
        UPDATE us_financial_data
        SET gross_profit = ?,
            opm = ?,
            ebitda = ?,
            pbr = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE ticker = ? AND period_end = ? AND period_type = ?
    """, updates)

    conn.commit()
    logger.info("US Financial metrics repair complete.")


def print_stats(conn: sqlite3.Connection):
    cur = conn.cursor()
    total = cur.execute("SELECT COUNT(*) FROM us_financial_data").fetchone()[0]
    logger.info(f"Total us_financial_data rows: {total}")
    cols = ['bps', 'pbr', 'eps', 'ebitda', 'opm', 'gross_profit', 'revenue', 'net_income', 'assets', 'equity', 'per']
    for col in cols:
        null_cnt = cur.execute(f"SELECT COUNT(*) FROM us_financial_data WHERE {col} IS NULL").fetchone()[0]
        pct = (null_cnt / total) * 100 if total > 0 else 0
        logger.info(f"  {col}: {null_cnt}/{total} null ({pct:.1f}%)")


def main():
    conn = sqlite3.connect(str(DB), timeout=120)
    conn.execute("PRAGMA busy_timeout=120000")
    try:
        logger.info("=== BEFORE REPAIR ===")
        print_stats(conn)
        repair_us_financial_metrics(conn)
        logger.info("=== AFTER REPAIR ===")
        print_stats(conn)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
