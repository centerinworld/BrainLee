from __future__ import annotations

import sqlite3
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = str(BASE_DIR / "stock.db")
HS_DB_PATH = str(BASE_DIR / "hs_trade_lab" / "data" / "hs_trade_lab.db")
router = APIRouter()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _hs_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(HS_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _latest_signal(conn: sqlite3.Connection, indicator_key: str) -> dict | None:
    row = conn.execute(
        """
        SELECT period, series_name, value, prev_value, mom_pct, yoy_pct,
               z_score, signal_type, signal_strength, message, generated_at
        FROM quant_indicator_signal_events
        WHERE indicator_key=?
        ORDER BY generated_at DESC, signal_strength DESC
        LIMIT 1
        """,
        (indicator_key,),
    ).fetchone()
    return dict(row) if row else None


def _indicator_hs_prefixes(conn: sqlite3.Connection, indicator_key: str) -> list[str]:
    row = conn.execute(
        """
        SELECT source_detail
        FROM quant_major_indicator_series
        WHERE indicator_key=? AND source_detail LIKE '%HS prefix%'
        ORDER BY period DESC LIMIT 1
        """,
        (indicator_key,),
    ).fetchone()
    if not row or not row["source_detail"]:
        return []
    match = re.search(r"HS prefix\s+([0-9,]+)", row["source_detail"])
    return [part.strip() for part in match.group(1).split(",") if part.strip()] if match else []


def _matching_hs(items: list[dict], prefixes: list[str]) -> list[dict]:
    if not prefixes:
        return []
    return [item for item in items if any(str(item.get("hs_code") or "").startswith(prefix) for prefix in prefixes)]


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


@router.get("/cross-context/{indicator_key:path}")
def get_indicator_cross_context(
    indicator_key: str,
    limit: int = Query(default=40, ge=1, le=200),
):
    """퀀트 지표에 연결된 종목을 HS Trade 매핑과 교차 검증한다."""
    conn = _conn()
    hs_conn = _hs_conn()
    try:
        meta = conn.execute(
            """
            SELECT indicator_key, epic_indicator_name, epic_category_code, status,
                   source_system, exactness
            FROM quant_major_indicator_catalog WHERE indicator_key=?
            """,
            (indicator_key,),
        ).fetchone()
        mappings = conn.execute(
            """
            SELECT stock_code, stock_name, sector_name, indicator_name, mention_count,
                   confidence, mapping_status, importance_level,
                   revenue_exposure_pct, profit_exposure_pct, cost_exposure_pct,
                   exposure_basis
            FROM cafe_stock_indicator_mappings
            WHERE indicator_key=?
            ORDER BY CASE mapping_status
                       WHEN 'confirmed_exposure' THEN 0
                       WHEN 'confirmed_relationship' THEN 1
                       ELSE 2 END,
                     confidence DESC, mention_count DESC
            LIMIT ?
            """,
            (indicator_key, limit),
        ).fetchall()
        codes = [row["stock_code"] for row in mappings]
        indicator_prefixes = _indicator_hs_prefixes(conn, indicator_key)
        hs_by_code: dict[str, list[dict]] = {code: [] for code in codes}
        if codes:
            placeholders = ",".join("?" for _ in codes)
            hs_rows = hs_conn.execute(
                f"""
                SELECT h.stock_code, h.hs_code, h.hs_name, h.sector_name,
                       h.mapping_status, h.flow_type, h.confidence,
                       h.market_share_pct, s.sector_key, s.display_name
                FROM hs_code_company_map h
                LEFT JOIN hs_sector_map s ON s.hs_code=h.hs_code
                WHERE h.stock_code IN ({placeholders})
                ORDER BY h.stock_code,
                         CASE h.mapping_status WHEN 'exact' THEN 0 WHEN 'composite' THEN 1 ELSE 2 END,
                         h.confidence DESC
                """,
                codes,
            ).fetchall()
            for row in hs_rows:
                hs_by_code.setdefault(row["stock_code"], []).append(dict(row))

        items = []
        for row in mappings:
            item = dict(row)
            hs_items = hs_by_code.get(row["stock_code"], [])
            matched_hs = _matching_hs(hs_items, indicator_prefixes)
            item["hs_mappings"] = matched_hs[:8]
            item["all_stock_hs_mapping_count"] = len(hs_items)
            item["cross_validation"] = "cross_confirmed" if matched_hs else "quant_only"
            item["cross_note"] = (
                "퀀트 지표 HS prefix와 기업 HS 코드가 직접 일치"
                if matched_hs else "동일 종목의 HS 매핑은 있어도 이 지표 품목군과 직접 일치하지 않음"
            )
            items.append(item)
        return {
            "indicator": dict(meta) if meta else None,
            "latest_signal": _latest_signal(conn, indicator_key),
            "hs_prefixes": indicator_prefixes,
            "summary": {
                "quant_stocks": len(items),
                "cross_confirmed": sum(1 for item in items if item["cross_validation"] == "cross_confirmed"),
                "quant_only": sum(1 for item in items if item["cross_validation"] == "quant_only"),
            },
            "items": items,
        }
    finally:
        hs_conn.close()
        conn.close()


@router.get("/stock-context/{stock_code}")
def get_stock_cross_context(stock_code: str, limit: int = Query(default=20, ge=1, le=100)):
    """HS Trade 종목 화면에서 사용할 퀀트 지표·HS 교차 맥락."""
    conn = _conn()
    hs_conn = _hs_conn()
    try:
        quant_rows = conn.execute(
            """
            SELECT indicator_key, indicator_name, sector_name, mention_count,
                   confidence, mapping_status, importance_level,
                   revenue_exposure_pct, profit_exposure_pct, cost_exposure_pct,
                   exposure_basis
            FROM cafe_stock_indicator_mappings
            WHERE stock_code=?
            ORDER BY CASE mapping_status
                       WHEN 'confirmed_exposure' THEN 0
                       WHEN 'confirmed_relationship' THEN 1
                       ELSE 2 END,
                     confidence DESC, mention_count DESC
            LIMIT ?
            """,
            (stock_code, limit),
        ).fetchall()
        hs_rows = hs_conn.execute(
            """
            SELECT h.hs_code, h.hs_name, h.sector_name, h.mapping_status,
                   h.flow_type, h.confidence, h.market_share_pct,
                   s.sector_key, s.display_name
            FROM hs_code_company_map h
            LEFT JOIN hs_sector_map s ON s.hs_code=h.hs_code
            WHERE h.stock_code=?
            ORDER BY CASE h.mapping_status WHEN 'exact' THEN 0 WHEN 'composite' THEN 1 ELSE 2 END,
                     h.confidence DESC
            LIMIT 30
            """,
            (stock_code,),
        ).fetchall()
        quant_items = []
        hs_items = [dict(row) for row in hs_rows]
        for row in quant_rows:
            item = dict(row)
            item["latest_signal"] = _latest_signal(conn, row["indicator_key"])
            prefixes = _indicator_hs_prefixes(conn, row["indicator_key"])
            item["hs_prefixes"] = prefixes
            item["matching_hs_mappings"] = _matching_hs(hs_items, prefixes)
            item["cross_validation"] = "cross_confirmed" if item["matching_hs_mappings"] else "quant_only"
            quant_items.append(item)
        cross_count = sum(1 for item in quant_items if item["cross_validation"] == "cross_confirmed")
        return {
            "stock_code": stock_code,
            "cross_validation": "cross_confirmed" if cross_count else (
                "quant_only" if quant_items else "hs_only" if hs_rows else "unmapped"
            ),
            "quant_indicators": quant_items,
            "hs_mappings": hs_items,
            "summary": {
                "quant_indicator_count": len(quant_items),
                "hs_mapping_count": len(hs_rows),
                "cross_confirmed_indicator_count": cross_count,
                "confirmed_quant_count": sum(
                    1 for item in quant_items
                    if item.get("mapping_status") in {"confirmed_exposure", "confirmed_relationship"}
                ),
            },
        }
    finally:
        hs_conn.close()
        conn.close()


@router.get("/hs-sector-context/{sector_key}")
def get_hs_sector_quant_context(sector_key: str):
    """HS Trade 섹터의 실제 HS 코드와 직접 겹치는 퀀트 지표를 반환한다."""
    conn = _conn()
    hs_conn = _hs_conn()
    try:
        hs_rows = hs_conn.execute(
            """
            SELECT DISTINCT hs_code, COALESCE(NULLIF(display_name,''), hs_name) AS hs_name,
                            mapping_status
            FROM hs_sector_map
            WHERE sector_key=?
            ORDER BY hs_code
            """,
            (sector_key,),
        ).fetchall()
        hs_items = [dict(row) for row in hs_rows]
        catalog = conn.execute(
            """
            SELECT indicator_key, epic_indicator_name, status, exactness
            FROM quant_major_indicator_catalog
            WHERE indicator_key LIKE 'public:23:%'
            ORDER BY epic_sub_code
            """
        ).fetchall()
        indicators = []
        for row in catalog:
            prefixes = _indicator_hs_prefixes(conn, row["indicator_key"])
            matched = _matching_hs(hs_items, prefixes)
            if not matched:
                continue
            item = dict(row)
            item["hs_prefixes"] = prefixes
            item["matching_hs"] = matched
            item["latest_signal"] = _latest_signal(conn, row["indicator_key"])
            indicators.append(item)
        return {
            "sector_key": sector_key,
            "hs_mapping_count": len(hs_items),
            "quant_indicator_count": len(indicators),
            "indicators": indicators,
        }
    finally:
        hs_conn.close()
        conn.close()
