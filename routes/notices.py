"""
routes/notices.py — 종목 공지사항 (KRX 종목기본정보 변동) API

  GET  /api/notices/stock/{code}     개별 종목 공지사항 목록 (최근순)
  GET  /api/notices/recent           전체 종목 최근 공지 (홈/대시보드용)
  GET  /api/notices/today            오늘 발생한 변동 (없으면 최근 영업일)

데이터 소스: stock_base_info_changes (KRX 일일 수집 기반)
변동 유형:
  - shares_issued : 상장주식수 변경 (무상증자/감자)
  - sector_type   : 소속부 변경 (관리종목/우량주 지정·해제)
  - secugrp_nm    : 증권 구분 변경
  - stock_name    : 종목명 변경
"""

from __future__ import annotations

import logging
import sqlite3 as _sl
from datetime import date, timedelta

from fastapi import APIRouter, Query

from db_utils import STOCK_DB_PATH

logger = logging.getLogger(__name__)
router = APIRouter()
DB_PATH = str(STOCK_DB_PATH)


def _conn():
    c = _sl.connect(DB_PATH, timeout=20)
    c.row_factory = _sl.Row
    return c


@router.get("/stock/{code}")
def stock_notices(code: str, limit: int = Query(20, ge=1, le=200)):
    """개별 종목 공지사항 (최근순)."""
    conn = _conn()
    try:
        rows = conn.execute("""
            SELECT change_date, change_type, old_value, new_value, description, created_at
            FROM stock_base_info_changes
            WHERE stock_code = ?
            ORDER BY change_date DESC, id DESC
            LIMIT ?
        """, (code, limit)).fetchall()
        return {
            "stock_code": code,
            "count": len(rows),
            "notices": [dict(r) for r in rows],
        }
    finally:
        conn.close()


@router.get("/recent")
def recent_notices(
    days: int = Query(7, ge=1, le=60),
    limit: int = Query(50, ge=1, le=500),
    change_type: str | None = Query(None, description="필터링: shares_issued, sector_type 등"),
):
    """최근 N일 전체 종목 변동사항 (대시보드 통합)."""
    since = (date.today() - timedelta(days=days)).isoformat()
    conn = _conn()
    try:
        params: list = [since]
        sql = """
            SELECT c.stock_code, c.change_date, c.change_type, c.old_value, c.new_value,
                   c.description, u.stock_name, u.market
            FROM stock_base_info_changes c
            LEFT JOIN stock_universe u ON u.stock_code = c.stock_code
            WHERE c.change_date >= ?
        """
        if change_type:
            sql += " AND c.change_type = ?"
            params.append(change_type)
        sql += " ORDER BY c.change_date DESC, c.id DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        return {
            "since": since,
            "count": len(rows),
            "notices": [dict(r) for r in rows],
        }
    finally:
        conn.close()


@router.get("/today")
def today_notices(limit: int = Query(50, ge=1, le=500)):
    """오늘(또는 최근 영업일)에 발생한 변동만."""
    conn = _conn()
    try:
        latest = conn.execute(
            "SELECT MAX(change_date) FROM stock_base_info_changes"
        ).fetchone()[0]
        if not latest:
            return {"date": None, "count": 0, "notices": []}

        rows = conn.execute("""
            SELECT c.stock_code, c.change_date, c.change_type, c.old_value, c.new_value,
                   c.description, u.stock_name, u.market
            FROM stock_base_info_changes c
            LEFT JOIN stock_universe u ON u.stock_code = c.stock_code
            WHERE c.change_date = ?
            ORDER BY c.id DESC
            LIMIT ?
        """, (latest, limit)).fetchall()
        return {
            "date": latest,
            "count": len(rows),
            "notices": [dict(r) for r in rows],
        }
    finally:
        conn.close()
