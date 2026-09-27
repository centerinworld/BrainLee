"""routes/hub.py — 허브(첫 화면) 모듈 상태 (2026-09-27, 사이트 전면 개편)

GET /api/hub/status → 4개 모듈(Stock Info / Key Indicator / Stock Lab / Stock LLM)의 가동 여부. 공개 조회이며 up/down·응답시간만 돌려준다(내부 주소·내용 노출 없음).
프로브는 15초 캐시 — 허브를 여러 명이 열어도 내부 서비스에 부하를 주지 않는다.
"""
import asyncio
import os
import time

import httpx
from fastapi import APIRouter

router = APIRouter()
_PROBES = {
    "key": os.environ.get("HUB_KEY_PROBE", "http://127.0.0.1:5500/"),                 # newsinfo.cloud 정적 서버
    "llm": os.environ.get("HUB_LLM_PROBE", "http://127.0.0.1:8888/api/health"),       # Brian_RAG
}
_CACHE: dict = {"at": 0.0, "data": None}
TTL = 15.0


async def _probe(client: httpx.AsyncClient, url: str) -> dict:
    t0 = time.perf_counter()
    try:
        r = await client.get(url)
        return {"up": r.status_code < 500, "ms": round((time.perf_counter() - t0) * 1000)}
    except httpx.HTTPError:
        return {"up": False, "ms": None}


@router.get("/status")
async def hub_status():
    now = time.time()
    if _CACHE["data"] is not None and now - _CACHE["at"] < TTL:
        return _CACHE["data"]
    async with httpx.AsyncClient(timeout=1.5) as client:
        keys = list(_PROBES)
        res = await asyncio.gather(*[_probe(client, _PROBES[k]) for k in keys])
    modules = dict(zip(keys, res))
    modules["info"] = {"up": True, "ms": 0}      # 이 응답을 만든 서버 자신
    modules["lab"] = {"up": True, "ms": 0}
    data = {"modules": modules, "checked_at": int(now)}
    _CACHE.update({"at": now, "data": data})
    return data
