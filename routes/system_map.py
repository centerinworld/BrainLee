"""routes/system_map.py — 관리자용 시스템 전체 지도 API (2026-09-27)

GET /api/sysmap/overview?fp=<직전 fingerprint>  → system_map.overview(): 소스가 바뀌었을 때만 static(코드·API·잡·계보)을 다시 보낸다.
내부 구조(엔드포인트·테이블·경로)를 담으므로 security_gate 가 `/api/sysmap` 을 관리자 전용으로 취급한다.
"""
from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

import system_map

router = APIRouter()


@router.get("/overview")
async def overview(request: Request, fp: str = ""):
    return await run_in_threadpool(system_map.overview, fp, request.app)
