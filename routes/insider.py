"""
routes/insider.py — 대주주 + 임원지분변동 API

  GET  /api/insider/major/{code}          종목별 5%+ 대량보유 보고 목록
  GET  /api/insider/holdings/{code}       종목별 임원·주요주주 보고 목록
  GET  /api/insider/recent-significant    최근 N일 주요 임원지분 변동 (전체)
  GET  /api/insider/ceo-changes           최근 대표이사 지분 변동 (홈/주요지표용)
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


@router.get("/major/{code}")
def major_holders(code: str, limit: int = Query(50, ge=1, le=300)):
    """종목별 대량보유 보고 (최근순). 같은 보고자별로 변동 흐름 추적."""
    conn = _conn()
    try:
        rows = conn.execute("""
            SELECT rcept_no, rcept_dt, repror, stkqy, stkrt,
                   ctr_stkqy, ctr_stkrt, stk_diff, rt_diff, report_tp
            FROM dart_major_holders
            WHERE stock_code = ?
            ORDER BY rcept_dt DESC, id DESC
            LIMIT ?
        """, (code, limit)).fetchall()

        # 보고자별 최근 보유 비율 (현재 대주주 명단)
        latest = conn.execute("""
            SELECT repror, stkqy, stkrt, rcept_dt FROM (
                SELECT repror, stkqy, stkrt, rcept_dt,
                       ROW_NUMBER() OVER (PARTITION BY repror ORDER BY rcept_dt DESC, id DESC) AS rn
                FROM dart_major_holders
                WHERE stock_code = ?
            ) WHERE rn = 1
            ORDER BY stkrt DESC NULLS LAST
            LIMIT 20
        """, (code,)).fetchall()

        return {
            "stock_code": code,
            "current_holders": [dict(r) for r in latest],
            "history": [dict(r) for r in rows],
        }
    finally:
        conn.close()


@router.get("/holdings/{code}")
def insider_holdings(code: str, limit: int = Query(50, ge=1, le=300)):
    """종목별 임원·주요주주 변동 보고 (최근순)."""
    conn = _conn()
    try:
        rows = conn.execute("""
            SELECT rcept_no, rcept_dt, repror,
                   isu_exctv_rgist, isu_exctv_ofcps, isu_main_shrholdr,
                   sp_stock_lmp_cnt, sp_stock_lmp_irds_cnt, sp_stock_lmp_irds_rate,
                   is_ceo, is_significant, change_amount
            FROM dart_insider_holdings
            WHERE stock_code = ?
            ORDER BY rcept_dt DESC, id DESC
            LIMIT ?
        """, (code, limit)).fetchall()
        return {
            "stock_code": code,
            "count": len(rows),
            "items": [dict(r) for r in rows],
        }
    finally:
        conn.close()


@router.get("/recent-significant")
def recent_significant(
    days: int = Query(7, ge=1, le=60),
    limit: int = Query(50, ge=1, le=300),
    ceo_only: bool = Query(False, description="대표이사만"),
):
    """
    최근 주요 임원지분 변동 (1억원+ 또는 비율 0.1%p+).
    "주요지표" 페이지 하단에 표시.
    """
    since = (date.today() - timedelta(days=days)).isoformat()
    conn = _conn()
    try:
        sql = """
            SELECT h.stock_code, h.rcept_dt, h.repror,
                   h.isu_exctv_ofcps, h.isu_main_shrholdr,
                   h.sp_stock_lmp_irds_cnt, h.sp_stock_lmp_irds_rate,
                   h.is_ceo, h.change_amount,
                   u.stock_name, u.market
            FROM dart_insider_holdings h
            LEFT JOIN stock_universe u ON u.stock_code = h.stock_code
            WHERE h.is_significant = 1
              AND h.rcept_dt >= ?
        """
        params: list = [since]
        if ceo_only:
            sql += " AND h.is_ceo = 1"
        sql += " ORDER BY ABS(COALESCE(h.change_amount,0)) DESC, h.rcept_dt DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        return {
            "since": since,
            "ceo_only": ceo_only,
            "count": len(rows),
            "items": [dict(r) for r in rows],
        }
    finally:
        conn.close()


@router.get("/ceo-changes")
def ceo_changes(
    days: int = Query(14, ge=1, le=90),
    limit: int = Query(30, ge=1, le=200),
):
    """최근 대표이사 지분 변동만 (홈 대시보드용 핵심 알림)."""
    since = (date.today() - timedelta(days=days)).isoformat()
    conn = _conn()
    try:
        rows = conn.execute("""
            SELECT h.stock_code, h.rcept_dt, h.repror, h.isu_exctv_ofcps,
                   h.sp_stock_lmp_irds_cnt, h.sp_stock_lmp_irds_rate,
                   h.change_amount, u.stock_name, u.market
            FROM dart_insider_holdings h
            LEFT JOIN stock_universe u ON u.stock_code = h.stock_code
            WHERE h.is_ceo = 1
              AND h.rcept_dt >= ?
              AND h.sp_stock_lmp_irds_cnt IS NOT NULL
              AND h.sp_stock_lmp_irds_cnt != 0
            ORDER BY h.rcept_dt DESC, ABS(COALESCE(h.change_amount,0)) DESC
            LIMIT ?
        """, (since, limit)).fetchall()
        return {
            "since": since,
            "count": len(rows),
            "items": [dict(r) for r in rows],
        }
    finally:
        conn.close()
