"""
routes/consensus.py — 한경 컨센서스 API

GET /api/consensus/{stock_code}         종목별 컨센서스 이력 (최대 24개월)
GET /api/consensus/{stock_code}/summary 최근 3개월 요약 (평균/최고/최저 목표주가 + 의견 분포)
GET /api/consensus/recent               최근 N일 전체 컨센서스 목록 (페이징)
POST /api/consensus/refresh             즉시 증분 수집 (최근 3일)
POST /api/consensus/backfill            2년치 전체 수집 (초기화용)
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks

logger = logging.getLogger(__name__)
router = APIRouter()

DB_PATH   = "stock.db"
_backfill_lock = threading.Lock()
_backfill_running = False


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/consensus/{stock_code}  — 종목별 컨센서스 이력
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{stock_code}")
def get_consensus(
    stock_code: str,
    months:     int = Query(24, ge=1, le=36, description="조회 개월수"),
    limit:      int = Query(200, ge=1, le=500),
):
    """
    특정 종목의 컨센서스 이력을 최신순으로 반환.
    {
      stock_code, stock_name, summary: {...}, records: [...]
    }
    """
    since = (date.today() - timedelta(days=months * 30)).strftime("%Y-%m-%d")
    conn  = _conn()
    try:
        rows = conn.execute(
            """
            SELECT id, report_date, securities_firm, analyst, opinion,
                   target_price, prev_target_price, skin_type, report_title, report_idx,
                   stock_name
            FROM   consensus_targets
            WHERE  stock_code = ?
              AND  report_date >= ?
              AND  target_price > 0
            ORDER  BY report_date DESC, id DESC
            LIMIT  ?
            """,
            (stock_code, since, limit),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {
            "stock_code": stock_code,
            "stock_name": "",
            "summary":    _empty_summary(),
            "records":    [],
        }

    records    = [dict(r) for r in rows]
    stock_name = records[0].get("stock_name", "") or ""
    summary    = _calc_summary(records)

    return {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "summary":    summary,
        "records":    records,
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/consensus/{stock_code}/summary  — 요약만
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{stock_code}/summary")
def get_consensus_summary(
    stock_code: str,
    months:     int = Query(3, ge=1, le=24),
):
    since = (date.today() - timedelta(days=months * 30)).strftime("%Y-%m-%d")
    conn  = _conn()
    try:
        rows = conn.execute(
            """
            SELECT report_date, securities_firm, opinion, target_price, stock_name
            FROM   consensus_targets
            WHERE  stock_code = ?
              AND  report_date >= ?
              AND  target_price > 0
            ORDER  BY report_date DESC
            """,
            (stock_code, since),
        ).fetchall()
    finally:
        conn.close()

    records    = [dict(r) for r in rows]
    stock_name = records[0].get("stock_name", "") if records else ""
    return {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "months":     months,
        **_calc_summary(records),
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/consensus/recent  — 최근 목록 (전체 종목)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/recent")
def get_recent_consensus(
    days:   int   = Query(7,  ge=1, le=90),
    limit:  int   = Query(100, ge=1, le=500),
    offset: int   = Query(0,  ge=0),
    skin:   Optional[str] = Query(None, description="business|stock_good|stock_bad"),
):
    since = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn  = _conn()
    try:
        where_skin = "AND skin_type = ?" if skin else ""
        params: list = [since]
        if skin:
            params.append(skin)
        params += [limit, offset]

        rows = conn.execute(
            f"""
            SELECT report_date, stock_code, stock_name, securities_firm,
                   analyst, opinion, target_price, prev_target_price, skin_type
            FROM   consensus_targets
            WHERE  report_date >= ?
              {where_skin}
              AND  target_price > 0
            ORDER  BY report_date DESC, id DESC
            LIMIT  ? OFFSET ?
            """,
            params,
        ).fetchall()

        total = conn.execute(
            f"""
            SELECT COUNT(*) FROM consensus_targets
            WHERE  report_date >= ?
              {where_skin}
              AND  target_price > 0
            """,
            [since] + ([skin] if skin else []),
        ).fetchone()[0]
    finally:
        conn.close()

    return {
        "days":    days,
        "total":   total,
        "limit":   limit,
        "offset":  offset,
        "records": [dict(r) for r in rows],
    }


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/consensus/refresh  — 즉시 증분 수집
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/refresh")
def refresh_consensus(background_tasks: BackgroundTasks):
    """최근 3일치 증분 수집을 백그라운드로 실행."""
    background_tasks.add_task(_run_collect, days=3, full=False)
    return {"status": "started", "message": "최근 3일 컨센서스 수집 시작"}


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/consensus/backfill  — 2년치 전체 수집 (초기화용)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/backfill")
def backfill_consensus(background_tasks: BackgroundTasks):
    """2년치 전체 수집을 백그라운드로 실행 (중복 실행 방지)."""
    global _backfill_running
    if _backfill_running:
        raise HTTPException(status_code=409, detail="이미 수집 중입니다")
    background_tasks.add_task(_run_collect, days=730, full=True)
    return {"status": "started", "message": "2년치 컨센서스 전체 수집 시작 (수십 분 소요)"}


def _run_collect(days: int = 3, full: bool = False):
    global _backfill_running
    if full:
        if not _backfill_lock.acquire(blocking=False):
            logger.warning("[컨센서스] 이미 수집 중 — 스킵")
            return
        _backfill_running = True
        try:
            from collectors.hankyung_consensus_collector import collect_consensus
            collect_consensus(db_path=DB_PATH, days=days, full=full)
        except Exception as e:
            logger.error(f"[컨센서스] 수집 오류: {e}", exc_info=True)
        finally:
            _backfill_running = False
            _backfill_lock.release()
    else:
        try:
            from collectors.hankyung_consensus_collector import collect_consensus
            collect_consensus(db_path=DB_PATH, days=days, full=False)
        except Exception as e:
            logger.error(f"[컨센서스] 수집 오류: {e}", exc_info=True)


# ─────────────────────────────────────────────────────────────────────────────
# 내부 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

def _empty_summary() -> dict:
    return {
        "avg_target": 0, "max_target": 0, "min_target": 0,
        "count": 0, "firms": [], "latest_date": None, "latest_target": 0,
        "buy_count": 0, "hold_count": 0, "sell_count": 0,
    }


def _calc_summary(records: list[dict]) -> dict:
    if not records:
        return _empty_summary()

    prices = [r["target_price"] for r in records if r.get("target_price")]
    firms  = list(dict.fromkeys(r["securities_firm"] for r in records if r.get("securities_firm")))

    _buy  = {"매수", "buy", "strong buy", "적극매수", "강력매수"}
    _hold = {"중립", "보유", "hold", "neutral", "시장수익률", "market perform"}
    _sell = {"매도", "sell", "underperform", "비중축소"}

    buy_cnt  = sum(1 for r in records if (r.get("opinion") or "").lower() in _buy)
    hold_cnt = sum(1 for r in records if (r.get("opinion") or "").lower() in _hold)
    sell_cnt = sum(1 for r in records if (r.get("opinion") or "").lower() in _sell)

    return {
        "avg_target":    round(sum(prices) / len(prices)) if prices else 0,
        "max_target":    int(max(prices)) if prices else 0,
        "min_target":    int(min(prices)) if prices else 0,
        "count":         len(records),
        "firms":         firms[:15],
        "latest_date":   records[0].get("report_date") if records else None,
        "latest_target": records[0].get("target_price") if records else 0,
        "buy_count":     buy_cnt,
        "hold_count":    hold_cnt,
        "sell_count":    sell_cnt,
    }
