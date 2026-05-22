"""
routes/kiwoom.py — 키움 REST 연동 상태 점검 API
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from collectors.kiwoom_collector import KiwoomCollector

router = APIRouter()


@router.get("/status")
def kiwoom_status():
    kc = KiwoomCollector()
    return {
        "enabled": kc.enabled,
        "configured": kc.is_configured(),
        "health": kc.health_check(),
    }


@router.post("/token/refresh")
def kiwoom_token_refresh():
    kc = KiwoomCollector()
    return kc.issue_token()


@router.post("/realtime/snapshot")
def kiwoom_realtime_snapshot(
    codes: str = Query(..., description="쉼표 구분 종목코드. 예: 005930,000660"),
    types: str = Query("0A,0B,0C", description="쉼표 구분 실시간 타입"),
    duration_sec: int = Query(12, ge=3, le=60),
):
    kc = KiwoomCollector()
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    type_list = [t.strip() for t in types.split(",") if t.strip()]
    return kc.collect_realtime_snapshot(stock_codes=code_list, types=type_list, duration_sec=duration_sec)


@router.post("/foreign-flow")
def kiwoom_foreign_flow(code: str = Query(..., description="종목코드 6자리")):
    kc = KiwoomCollector()
    return kc.fetch_foreign_flow(code.strip())
