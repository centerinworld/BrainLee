"""security_gate.py — 터널(인터넷) 경유 요청에 대한 쓰기·민감 API 토큰 게이트 (HANDOFF §11 S0 ②, 2026-09-25)

배경: stock.leanguy.cloud(Cloudflare 터널) → vite preview(5173) → uvicorn(127.0.0.1:8000) 경로에 API 인증이 없어 쓰기 엔드포인트
(124개)와 /api/portfolio 등이 외부에 그대로 노출됐다. Cloudflare Access(① 사용자 설정)와 별개로 서버 쪽 방어선을 둔다.

규칙 (2026-09-26 V1 개정: 차단 목록 → 허용 목록 방식)
  · 검사 대상 = 터널을 거친 요청(Cloudflare가 항상 붙이는 `Cf-Connecting-Ip` 또는 `X-Forwarded-For`/`Cf-Ray` 헤더가 있음).
    서버 내부·스케줄러·스크립트의 로컬 호출(헤더 없음, 루프백)은 그대로 통과한다.
  · 보호 경로 = /api·/hs·/semiconductor-lab 아래의 **모든 메서드(GET 포함)** + API 문서(/docs·/redoc·/openapi.json).
    이전 방식(쓰기 + 민감 GET 5개 접두사)은 /api/realtime/prices(실보유 47종목)·/api/buy-candidates·/api/trend/holdings 등이 새어 나갔다.
  · 공개가 필요한 GET은 PUBLIC_GET_PREFIXES 허용 목록에만 추가한다(현재 없음). 신규 엔드포인트는 자동으로 보호된다.
  · 토큰 = 환경변수 API_WRITE_TOKEN. 요청 헤더 `X-API-Token` 또는 `Authorization: Bearer <token>`과 상수시간 비교.
  · 토큰이 서버에 설정돼 있지 않으면 보호 경로의 터널 요청은 전부 거부한다(fail-closed, 503).
  · OPTIONS(CORS 사전요청)는 통과한다. 프런트 정적 파일(/, /assets/*)은 보호 대상이 아니다.
"""
from __future__ import annotations

import hmac
import os

from fastapi import Request
from fastapi.responses import JSONResponse

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
API_PREFIXES = ("/api", "/hs", "/semiconductor-lab")
DOC_PATHS = ("/docs", "/redoc", "/openapi.json")
PUBLIC_GET_PREFIXES: tuple[str, ...] = ()      # 터널 경유에서도 토큰 없이 허용할 GET 접두사(현재 없음)


def _via_tunnel(request: Request) -> bool:
    h = request.headers
    return bool(h.get("cf-connecting-ip") or h.get("x-forwarded-for") or h.get("cf-ray"))


def is_protected(method: str, path: str) -> bool:
    method = method.upper()
    if method == "OPTIONS":
        return False
    if method in ("GET", "HEAD") and PUBLIC_GET_PREFIXES and path.startswith(PUBLIC_GET_PREFIXES):
        return False
    return path.startswith(API_PREFIXES) or path in DOC_PATHS


def _presented_token(request: Request) -> str:
    tok = request.headers.get("x-api-token", "")
    if not tok:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            tok = auth[7:].strip()
    return tok


async def api_token_gate(request: Request, call_next):
    if not (_via_tunnel(request) and is_protected(request.method, request.url.path)):
        return await call_next(request)
    expected = os.environ.get("API_WRITE_TOKEN", "")
    if not expected:
        return JSONResponse({"detail": "api_token_not_configured"}, status_code=503)
    if not hmac.compare_digest(_presented_token(request).encode(), expected.encode()):
        return JSONResponse({"detail": "api_token_required"}, status_code=401,
                            headers={"WWW-Authenticate": "Bearer"})
    return await call_next(request)
