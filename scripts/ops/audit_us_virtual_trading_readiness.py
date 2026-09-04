#!/usr/bin/env python3
"""
Audit US-stock data freshness and virtual-trading compatibility.

This is intentionally read-only. It catches the two failure modes that matter
most for US virtual trading:
1) US prices/factors look fresh because the job ran today, but most tickers are
   still priced on an older trading date.
2) US tickers are present in the Korean virtual-trading tables without currency
   and market safeguards.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "stock.db"
OUT_DIR = PROJECT_ROOT / "research_outputs"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def one(conn: sqlite3.Connection, sql: str, params: tuple = ()):
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else {}


def many(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def audit() -> dict:
    conn = connect()
    try:
        tables = {
            "us_stock_meta": table_exists(conn, "us_stock_meta"),
            "us_price_history": table_exists(conn, "us_price_history"),
            "us_financial_data": table_exists(conn, "us_financial_data"),
            "us_cashflow_data": table_exists(conn, "us_cashflow_data"),
            "us_disclosures": table_exists(conn, "us_disclosures"),
            "us_factor_snapshot": table_exists(conn, "us_factor_snapshot"),
            "peak_holding": table_exists(conn, "peak_holding"),
            "us_paper_positions": table_exists(conn, "us_paper_positions"),
            "us_paper_orders": table_exists(conn, "us_paper_orders"),
            "us_paper_cash_ledger": table_exists(conn, "us_paper_cash_ledger"),
        }

        coverage = {}
        if tables["us_price_history"]:
            coverage["price_history"] = one(
                conn,
                """
                SELECT COUNT(*) AS rows,
                       COUNT(DISTINCT ticker) AS tickers,
                       MIN(date) AS min_date,
                       MAX(date) AS max_date
                FROM us_price_history
                """,
            )
            coverage["latest_dates"] = many(
                conn,
                """
                SELECT date, COUNT(DISTINCT ticker) AS tickers
                FROM us_price_history
                GROUP BY date
                ORDER BY date DESC
                LIMIT 8
                """,
            )
            coverage["latest_full_date_3000"] = one(
                conn,
                """
                SELECT date, tickers
                FROM (
                  SELECT date, COUNT(DISTINCT ticker) AS tickers
                  FROM us_price_history
                  GROUP BY date
                )
                WHERE tickers >= 3000
                ORDER BY date DESC
                LIMIT 1
                """,
            )
            max_date = coverage["price_history"].get("max_date")
            if max_date and tables["us_stock_meta"]:
                coverage["stale_top_market_cap"] = many(
                    conn,
                    """
                    WITH latest AS (
                      SELECT ticker, MAX(date) AS latest_date
                      FROM us_price_history
                      GROUP BY ticker
                    )
                    SELECT m.ticker, m.company_name, ROUND(m.market_cap,0) AS market_cap,
                           l.latest_date
                    FROM us_stock_meta m
                    LEFT JOIN latest l ON l.ticker=m.ticker
                    WHERE l.latest_date IS NULL OR l.latest_date < ?
                    ORDER BY COALESCE(m.market_cap,0) DESC
                    LIMIT 20
                    """,
                    (max_date,),
                )

        if tables["us_factor_snapshot"]:
            coverage["factor_snapshot"] = one(
                conn,
                """
                SELECT COUNT(*) AS rows,
                       COUNT(DISTINCT ticker) AS tickers,
                       MIN(as_of_date) AS min_as_of_date,
                       MAX(as_of_date) AS max_as_of_date
                FROM us_factor_snapshot
                """,
            )
            coverage["factor_dates"] = many(
                conn,
                """
                SELECT as_of_date, COUNT(*) AS tickers
                FROM us_factor_snapshot
                GROUP BY as_of_date
                ORDER BY as_of_date DESC
                LIMIT 8
                """,
            )

        if tables["us_financial_data"]:
            coverage["financial_data"] = one(
                conn,
                """
                SELECT COUNT(*) AS rows,
                       COUNT(DISTINCT ticker) AS tickers,
                       MIN(period_end) AS min_period,
                       MAX(period_end) AS max_period
                FROM us_financial_data
                """,
            )
        if tables["us_cashflow_data"]:
            coverage["cashflow_data"] = one(
                conn,
                """
                SELECT COUNT(*) AS rows,
                       COUNT(DISTINCT ticker) AS tickers,
                       MIN(period_end) AS min_period,
                       MAX(period_end) AS max_period
                FROM us_cashflow_data
                """,
            )

        virtual = {}
        if tables["peak_holding"]:
            virtual["us_positions"] = one(
                conn,
                """
                SELECT COUNT(*) AS rows,
                       SUM(CASE WHEN is_active=1 THEN 1 ELSE 0 END) AS active_rows
                FROM peak_holding
                WHERE stock_code IS NOT NULL
                  AND stock_code <> ''
                  AND stock_code NOT GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
                """,
            )
            virtual["us_position_samples"] = many(
                conn,
                """
                WITH latest AS (
                  SELECT ticker, MAX(date) AS latest_date
                  FROM us_price_history
                  GROUP BY ticker
                )
                SELECT p.id, p.strategy, p.stock_code, p.stock_name, p.buy_price,
                       p.current_price AS cached_current_price, p.quantity, p.is_active,
                       l.latest_date
                FROM peak_holding p
                LEFT JOIN latest l ON l.ticker=UPPER(p.stock_code)
                WHERE p.stock_code IS NOT NULL
                  AND p.stock_code <> ''
                  AND p.stock_code NOT GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
                ORDER BY p.is_active DESC, p.updated_at DESC
                LIMIT 30
                """,
            )

        if tables["us_paper_positions"]:
            virtual["dedicated_us_positions"] = one(
                conn,
                """
                SELECT COUNT(*) AS rows,
                       COALESCE(SUM(qty),0) AS total_qty,
                       COALESCE(SUM(qty * avg_price),0) AS cost_usd
                FROM us_paper_positions
                """
            )
        if tables["us_paper_orders"]:
            virtual["dedicated_us_orders"] = one(
                conn,
                """
                SELECT COUNT(*) AS rows,
                       MAX(ts) AS latest_order_ts
                FROM us_paper_orders
                """
            )
        if tables["us_paper_cash_ledger"]:
            virtual["dedicated_us_cash"] = one(
                conn,
                """
                SELECT balance_after AS cash_usd
                FROM us_paper_cash_ledger
                ORDER BY id DESC
                LIMIT 1
                """
            )

        issues: list[str] = []
        latest_dates = coverage.get("latest_dates") or []
        if latest_dates:
            top = latest_dates[0]
            full = coverage.get("latest_full_date_3000") or {}
            if top.get("tickers", 0) < 1000:
                issues.append(
                    f"US latest price date {top.get('date')} has only {top.get('tickers')} ticker(s); "
                    f"last broad date is {full.get('date')} ({full.get('tickers')} tickers)."
                )
        stale = coverage.get("stale_top_market_cap") or []
        if stale:
            issues.append(f"{len(stale)} top-market-cap stale ticker samples found; run stale-only sync.")
        us_pos = virtual.get("us_positions") or {}
        if us_pos.get("active_rows", 0):
            issues.append("US virtual positions exist in legacy peak_holding; migrate them to dedicated us_paper_positions.")

        recommendations = [
            "Run scripts/ops/sync_us_daily_quotes_and_factors.py --stale-only after every US market close.",
            "Do not route US tickers through /api/kis-trading/paper/order; use /api/us-virtual/order instead.",
            "Keep US paper trading in USD tables, then add a separate KRW-converted combined performance view.",
            "Rebuild us_factor_snapshot after stale price repair because as_of_date is now tied to actual latest price date.",
        ]

        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "database": str(DB_PATH),
            "tables": tables,
            "coverage": coverage,
            "virtual_trading": virtual,
            "issues": issues,
            "recommendations": recommendations,
        }
    finally:
        conn.close()


def write_report(result: dict) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"us_virtual_trading_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    result = audit()
    path = write_report(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\nwritten: {path}")
