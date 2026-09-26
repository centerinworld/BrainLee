"""routes/portfolio_access.py — 계좌현황 열람 로그인 (2026-09-26)

계좌현황 비밀번호를 **서버가 확인**하고 12시간짜리 서명 쿠키(`pf_view`)를 발급한다. 이전에는 비밀번호가 브라우저 코드에서만 비교돼 API를 직접 부르면 보호가 없었다.
비밀번호는 환경변수 PORTFOLIO_VIEW_PASSWORD. 무차별 대입 방지: IP당 10분에 8회.
  POST /api/portfolio-access/login   {"password": "..."}  -> 200 + Set-Cookie pf_view / 401 / 429 / 503
  GET  /api/portfolio-access/status  -> {"authenticated": bool}
  POST /api/portfolio-access/logout  -> 쿠키 삭제
"""
import hmac
import os
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import security_gate as sg

router = APIRouter()
_ATTEMPTS: dict[str, deque] = defaultdict(deque)
MAX_ATTEMPTS, WINDOW = 8, 600.0


def _too_many(ip: str, now: float | None = None) -> bool:
    t = now if now is not None else time.time()
    q = _ATTEMPTS[ip]
    while q and q[0] <= t - WINDOW:
        q.popleft()
    if len(q) >= MAX_ATTEMPTS:
        return True
    q.append(t)
    return False


def _secure(request: Request) -> bool:
    return sg._via_tunnel(request) or request.url.scheme == "https"


@router.post("/login")
async def login(request: Request):
    expected = os.environ.get("PORTFOLIO_VIEW_PASSWORD", "")
    if not expected or not sg._view_secret():
        return JSONResponse({"detail": "portfolio_view_not_configured"}, status_code=503)
    if _too_many(sg.client_ip(request)):
        return JSONResponse({"detail": "too_many_attempts"}, status_code=429, headers={"Retry-After": "300"})
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    given = str((body or {}).get("password", ""))
    if not hmac.compare_digest(given.encode(), expected.encode()):
        return JSONResponse({"detail": "wrong_password"}, status_code=401)
    resp = JSONResponse({"ok": True, "expires_in": sg.VIEW_TTL_SECONDS})
    resp.set_cookie(sg.VIEW_COOKIE, sg.make_view_cookie(), max_age=sg.VIEW_TTL_SECONDS, httponly=True, secure=_secure(request), samesite="lax", path="/")
    return resp


@router.get("/status")
async def status(request: Request):
    return {"authenticated": sg.valid_view_cookie(request.cookies.get(sg.VIEW_COOKIE, ""))}


@router.post("/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(sg.VIEW_COOKIE, path="/")
    return resp
