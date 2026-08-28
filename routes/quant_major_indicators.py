from __future__ import annotations

import sqlite3
from typing import Optional

from fastapi import APIRouter, Query

DB_PATH = "stock.db"
router = APIRouter()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@router.get("/catalog")
def get_quant_indicator_catalog(
    priority: str = Query(default="", description="p1/p2/p3"),
    status: str = Query(default="", description="ready_existing/new_collector_needed/..."),
):
    conn = _conn()
    try:
        sql = """
        SELECT indicator_key, epic_category_code, epic_sub_code, epic_indicator_name,
               frequency, base_unit, status, replacement_family, source_system,
               collector_path, exactness, priority, notes, updated_at
        FROM quant_major_indicator_catalog
        WHERE 1=1
        """
        params: list[str] = []
        if priority:
            sql += " AND priority = ?"
            params.append(priority)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY priority, epic_category_code, epic_sub_code"
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        return {"count": len(rows), "catalog": rows, "items": rows}
    finally:
        conn.close()


@router.get("/series/{indicator_key:path}")
def get_quant_indicator_series(
    indicator_key: str,
    limit: int = Query(default=120, ge=1, le=2000),
):
    conn = _conn()
    try:
        meta = conn.execute(
            """
            SELECT indicator_key, epic_category_code, epic_sub_code, epic_indicator_name,
                   frequency, base_unit, status, replacement_family, source_system,
                   collector_path, exactness, priority, notes, updated_at
            FROM quant_major_indicator_catalog
            WHERE indicator_key = ?
            """,
            (indicator_key,),
        ).fetchone()
        rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT period, series_name, value, unit, source_name, source_detail, quality, updated_at
                FROM quant_major_indicator_series
                WHERE indicator_key = ?
                ORDER BY period DESC, series_name ASC
                LIMIT ?
                """,
                (indicator_key, limit),
            ).fetchall()
        ]
        return {
            "indicator": dict(meta) if meta else None,
            "count": len(rows),
            "items": rows,
        }
    finally:
        conn.close()


@router.get("/summary")
def get_quant_indicator_summary():
    conn = _conn()
    try:
        rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT c.priority, c.status, count(*) AS indicator_count
                FROM quant_major_indicator_catalog c
                GROUP BY c.priority, c.status
                ORDER BY c.priority, c.status
                """
            ).fetchall()
        ]
        ready_rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT indicator_key, period, series_name, value, unit, source_name
                FROM quant_major_indicator_series
                WHERE indicator_key IN ('epic:20:1', 'epic:20:22', 'epic:20:99')
                ORDER BY indicator_key, period DESC, series_name
                LIMIT 20
                """
            ).fetchall()
        ]
        return {"summary": rows, "sample_ready_series": ready_rows}
    finally:
        conn.close()
