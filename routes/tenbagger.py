"""
routes/tenbagger.py — 텐배거 헌터 API

  POST /api/tenbagger/analyze/{code}   단일 종목 AI 분석
  POST /api/tenbagger/scan             관심종목 일괄 스캔
  GET  /api/tenbagger/candidates       최근 스캔 결과 반환 (캐시)
"""

import logging
import sqlite3 as _sl
from datetime import datetime

from fastapi import APIRouter

import tenbagger_ai as _tb

logger = logging.getLogger(__name__)
router = APIRouter()

DB_PATH = "/Applications/stock_dashboard/stock.db"

# 스캔 결과 메모리 캐시 (서버 재시작 시 초기화)
_scan_cache: list = []


def _db():
    conn = _sl.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@router.post("/analyze/{code}")
async def analyze_stock(code: str, payload: dict = None):
    """단일 종목 텐배거 잠재성 AI 분석."""
    payload     = payload or {}
    as_of_date  = payload.get("date", datetime.today().strftime("%Y-%m-%d"))
    force       = payload.get("force_refresh", False)

    try:
        result = _tb.analyze_tenbagger_potential(code, as_of_date, force_refresh=force)
        return {"ok": True, **result}
    except Exception as e:
        logger.error("tenbagger analyze %s: %s", code, e)
        return {"ok": False, "error": str(e), "stock_code": code}


@router.post("/scan")
async def scan_tenbaggers(payload: dict = None):
    """
    관심종목 또는 전체 종목 중 텐배거 후보 스캔.
    payload:
      - codes: list[str] (지정 시 해당 목록만)
      - min_score: int (기본 50)
      - limit: int (기본 30)
      - scope: "watchlist" | "universe" | "all" (기본 watchlist)
    """
    global _scan_cache
    payload   = payload or {}
    min_score = int(payload.get("min_score", 50))
    limit     = int(payload.get("limit", 30))
    scope     = payload.get("scope", "watchlist")
    codes_in  = payload.get("codes")
    as_of     = payload.get("date", datetime.today().strftime("%Y-%m-%d"))

    conn = _db()
    try:
        if codes_in:
            codes = codes_in[:200]
        elif scope == "watchlist":
            rows  = conn.execute("SELECT stock_code FROM watchlist LIMIT 200").fetchall()
            codes = [r[0] for r in rows]
        elif scope == "universe":
            rows = conn.execute("""
                SELECT stock_code FROM stock_universe
                WHERE LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
                  AND (market_cap IS NULL OR market_cap <= 3000000000000)
                ORDER BY RANDOM() LIMIT 500
            """).fetchall()
            codes = [r[0] for r in rows]
        else:
            # buy_candidates + watchlist
            rows = conn.execute("""
                SELECT stock_code FROM (
                    SELECT stock_code FROM watchlist
                    UNION
                    SELECT stock_code FROM buy_candidates
                ) LIMIT 200
            """).fetchall()
            codes = [r[0] for r in rows]
    finally:
        conn.close()

    results = _tb.batch_analyze(codes, as_of_date=as_of, min_score=min_score)
    results  = results[:limit]
    _scan_cache = results

    return {
        "ok":       True,
        "scanned":  len(codes),
        "found":    len(results),
        "results":  results,
        "as_of":    as_of,
    }


@router.get("/candidates")
def get_candidates():
    """최근 스캔 결과 반환. 스캔 전이면 빈 목록."""
    return {
        "ok":      True,
        "count":   len(_scan_cache),
        "results": _scan_cache,
    }


@router.get("/watchlist-quick")
def quick_watchlist_scan():
    """관심종목 전체를 정량 지표만으로 빠르게 스캔 (API 미사용)."""
    conn = _db()
    try:
        rows = conn.execute("""
            SELECT w.stock_code,
                   COALESCE(su.stock_name, w.stock_code) AS stock_name,
                   COALESCE(su.sector_large, '기타')     AS sector,
                   su.market_cap
            FROM watchlist w
            LEFT JOIN stock_universe su USING(stock_code)
            LIMIT 200
        """).fetchall()
    finally:
        conn.close()

    today = datetime.today().strftime("%Y-%m-%d")
    results = []
    for code, name, sector, mktcap in rows:
        try:
            fund = _tb._get_fundamentals(code, today)
            curr = fund["curr_price"] or 0
            ma200 = fund["ma200"]
            high_250 = fund["high_250d"]
            vol60 = fund["vol60_avg"] or 1
            vol_today = fund["vol_today"] or 0
            vol_ratio = vol_today / vol60 if vol60 > 0 else 0

            rev = fund["revenue"]
            rev_prev = fund["revenue_prev"]
            op_profit = fund["op_profit"]
            rev_yoy = None
            if rev and rev_prev and rev_prev > 0:
                rev_yoy = (rev - rev_prev) / rev_prev * 100

            r = _tb._quant_only_verdict(
                code, name, curr, ma200, high_250,
                vol_ratio, op_profit, rev_yoy, mktcap, sector
            )
            r["stock_code"] = code
            r["stock_name"] = name
            r["sector"]     = sector
            r["curr_price"] = curr
            r["mktcap"]     = mktcap
            results.append(r)
        except Exception:
            continue

    results.sort(key=lambda x: -x.get("tenbagger_score", 0))
    return {"ok": True, "count": len(results), "results": results}
