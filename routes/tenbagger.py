"""
routes/tenbagger.py — 텐버거 종목 발굴 API

GET  /api/tenbagger/results           최신 발굴 회차 결과
GET  /api/tenbagger/history           발굴 이력 (회차 목록)
GET  /api/tenbagger/run-history       회차별 상세 결과
POST /api/tenbagger/run               수동 발굴 실행 (백그라운드)
GET  /api/tenbagger/status            마지막 실행 상태
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime
from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter()

DB_PATH = "stock.db"

_run_lock   = threading.Lock()
_run_status = {"running": False, "last_run": None, "last_count": 0, "error": None}


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table():
    try:
        from tenbagger_engine import init_tenbagger_tables
        init_tenbagger_tables()
    except Exception as e:
        logger.warning(f"[tenbagger] 테이블 초기화 오류: {e}")


# ──────────────────────────────────────────────
# 최신 결과 조회
# ──────────────────────────────────────────────

@router.get("/results")
def get_latest_results(limit: int = 20):
    """가장 최근 발굴 회차의 결과 목록."""
    _ensure_table()
    conn = _get_conn()
    try:
        # 최신 run_time 조회
        row = conn.execute(
            "SELECT run_time FROM tenbagger_results ORDER BY run_time DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {"run_time": None, "results": [], "count": 0}

        latest_run = row["run_time"]
        rows = conn.execute("""
            SELECT id, run_time, run_type, stock_code, stock_name,
                   total_score, score_detail, reasons, ai_analysis,
                   current_price, market_cap, per, pbr, roe,
                   revenue_growth, op_growth, op_margin,
                   inst_net_10d, frn_net_10d, telegram_sent, created_at
            FROM   tenbagger_results
            WHERE  run_time = ?
            ORDER  BY total_score DESC
            LIMIT  ?
        """, (latest_run, limit)).fetchall()

        results = []
        for r in rows:
            d = dict(r)
            d["reasons"]      = json.loads(d["reasons"] or "[]")
            d["score_detail"] = json.loads(d["score_detail"] or "{}")
            results.append(d)

        return {"run_time": latest_run, "results": results, "count": len(results)}
    finally:
        conn.close()


# ──────────────────────────────────────────────
# 발굴 이력 (회차 목록)
# ──────────────────────────────────────────────

@router.get("/history")
def get_history(limit: int = 30):
    """발굴 회차별 요약 목록 (최신 N회)."""
    _ensure_table()
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT run_time, run_type,
                   COUNT(*)         AS count,
                   MAX(total_score) AS max_score,
                   AVG(total_score) AS avg_score
            FROM   tenbagger_results
            GROUP  BY run_time, run_type
            ORDER  BY run_time DESC
            LIMIT  ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ──────────────────────────────────────────────
# 특정 회차 상세 조회
# ──────────────────────────────────────────────

@router.get("/run-history")
def get_run_detail(run_time: str, limit: int = 20):
    """특정 run_time의 발굴 결과 전체."""
    _ensure_table()
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT id, run_time, run_type, stock_code, stock_name,
                   total_score, score_detail, reasons, ai_analysis,
                   current_price, market_cap, per, pbr, roe,
                   revenue_growth, op_growth, op_margin,
                   inst_net_10d, frn_net_10d, telegram_sent, created_at
            FROM   tenbagger_results
            WHERE  run_time = ?
            ORDER  BY total_score DESC
            LIMIT  ?
        """, (run_time, limit)).fetchall()

        results = []
        for r in rows:
            d = dict(r)
            d["reasons"]      = json.loads(d["reasons"] or "[]")
            d["score_detail"] = json.loads(d["score_detail"] or "{}")
            results.append(d)

        return {"run_time": run_time, "results": results, "count": len(results)}
    finally:
        conn.close()


# ──────────────────────────────────────────────
# 수동 실행 트리거
# ──────────────────────────────────────────────

@router.post("/run")
def trigger_run():
    """수동으로 텐버거 발굴 즉시 실행 (백그라운드 스레드)."""
    global _run_status

    if _run_status["running"]:
        return {"status": "already_running", "message": "현재 발굴이 진행 중입니다."}

    def _bg():
        global _run_status
        _run_status["running"] = True
        _run_status["error"]   = None
        try:
            from tenbagger_engine import run_discovery
            results = run_discovery("manual")
            _run_status["last_run"]   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _run_status["last_count"] = len(results)
        except Exception as e:
            logger.error(f"[텐버거 수동실행] {e}", exc_info=True)
            _run_status["error"] = str(e)
        finally:
            _run_status["running"] = False

    t = threading.Thread(target=_bg, daemon=True, name="tenbagger-manual")
    t.start()

    return {"status": "started", "message": "발굴을 시작했습니다. 잠시 후 결과를 확인하세요."}


# ──────────────────────────────────────────────
# 실행 상태
# ──────────────────────────────────────────────

@router.get("/status")
def get_status():
    return {
        "running":    _run_status["running"],
        "last_run":   _run_status["last_run"],
        "last_count": _run_status["last_count"],
        "error":      _run_status["error"],
    }
