from __future__ import annotations

import sqlite3
from typing import Any

from .config import ROOT_STOCK_DB


def _connect_ro() -> sqlite3.Connection:
    uri = f"file:{ROOT_STOCK_DB}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def search_companies(query: str = "", limit: int = 30) -> list[dict[str, Any]]:
    sql = """
        SELECT DISTINCT
            stock_code,
            stock_name,
            COALESCE(market, '') AS market,
            COALESCE(sector_large, '') AS sector_large,
            COALESCE(sector_mid, '') AS sector_mid,
            COALESCE(sector_small, '') AS sector_small
        FROM stock_universe
        WHERE (? = '')
           OR stock_code LIKE '%' || ? || '%'
           OR stock_name LIKE '%' || ? || '%'
           OR sector_large LIKE '%' || ? || '%'
           OR sector_mid LIKE '%' || ? || '%'
           OR sector_small LIKE '%' || ? || '%'
        ORDER BY stock_name
        LIMIT ?
    """
    with _connect_ro() as conn:
        rows = conn.execute(sql, (query, query, query, query, query, query, limit)).fetchall()
    return [dict(row) for row in rows]


def list_sectors(limit: int = 100) -> list[str]:
    sql = """
        SELECT DISTINCT sector_name
        FROM sector_info
        WHERE sector_name IS NOT NULL AND TRIM(sector_name) <> ''
        ORDER BY sector_name
        LIMIT ?
    """
    with _connect_ro() as conn:
        rows = conn.execute(sql, (limit,)).fetchall()
    return [row["sector_name"] for row in rows]
