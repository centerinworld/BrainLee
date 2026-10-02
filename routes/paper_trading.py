"""routes/paper_trading.py — 전략센터 가상매매(sc_paper) API (2026-10-02)

엔드포인트:
  GET  /api/paper-trading/summary     — 계좌 요약 + 보유 포지션 + 최근 주문
  POST /api/paper-trading/intake      — 당일 신호 수동 등록 (관리자)
  POST /api/paper-trading/fill        — pending 주문 수동 체결 (관리자)
"""
from __future__ import annotations

from datetime import date

import db_compat
from fastapi import APIRouter, HTTPException

router = APIRouter()


def _conn():
    return db_compat.connect_primary_db()


@router.get("/api/paper-trading/summary")
def paper_trading_summary():
    """sc_paper 계좌 요약."""
    import paper_adapters as pa
    try:
        return pa.summary()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/paper-trading/intake")
def paper_trading_intake(signal_date: str | None = None):
    """당일(또는 지정일) BUY_CANDIDATE 신호를 paper_order_queue에 등록."""
    import paper_adapters as pa
    today = signal_date or date.today().isoformat()
    try:
        conn = _conn()
        cnt = pa.intake_signals(conn, as_of_date=today)
        conn.commit()
        conn.close()
        return {"status": "ok", "registered": cnt, "signal_date": today}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/paper-trading/fill")
def paper_trading_fill(fill_date: str | None = None):
    """pending 주문을 지정일(기본: 오늘) 시가로 체결."""
    import paper_adapters as pa
    today = fill_date or date.today().isoformat()
    try:
        conn = _conn()
        result = pa.fill_pending(conn, as_of_date=today)
        conn.close()
        return {
            "status": "ok",
            "fill_date": today,
            "bought": len(result["bought"]),
            "sold": len(result["sold"]),
            "unfilled": len(result["unfilled"]),
            "cancelled": len(result["cancelled"]),
            "carried": len(result["carried"]),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
