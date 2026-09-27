"""routes/admin_auth.py — 관리자 모드 로그인 (2026-09-27, 사이트 전면 개편 §관리자)

관리자 비밀번호를 **서버가 확인**하고 8시간짜리 서명 쿠키(`sd_admin`, HttpOnly)를 발급한다. 이 쿠키가 있으면 security_gate의 owner 단계(쓰기·내보내기·설정 등)와
`/llm` 프록시(Stock LLM)를 통과한다. 비밀번호는 `.env`의 `ADMIN_PASSWORD_HASH`(권장, `scripts/ops/set_admin_password.py`로 생성) 또는 `ADMIN_PASSWORD`(임시 평문).
둘 다 없으면 로그인은 503(fail-closed). 무차별 대입 방지: IP당 10분에 6회 + 실패 시 지연.
  POST /api/admin-auth/login   {"password": "..."}  -> 200 + Set-Cookie sd_admin / 401 / 429 / 503
  GET  /api/admin-auth/status  -> {"authenticated": bool, "configured": bool, "can_setup": bool}
  POST /api/admin-auth/setup   {"password": "..."}  -> 최초 1회 설정. **비밀번호가 아직 없고 + 이 PC에서 직접 접속한(인터넷 터널이 아닌) 요청일 때만** 허용
  POST /api/admin-auth/invest-unlock {"password"} -> 「내 투자」 잠금 해제 쿠키 sd_invest(30분) — 관리자 로그인 여부와 무관하게 비밀번호를 다시 확인
  GET  /api/admin-auth/invest-status -> {"unlocked": bool} · POST /invest-lock -> 다시 잠금
  POST /api/admin-auth/logout  -> 쿠키 삭제
"""
import asyncio
import os
import re
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import security_gate as sg

router = APIRouter()
_ATTEMPTS: dict[str, deque] = defaultdict(deque)
MAX_ATTEMPTS, WINDOW = 6, 600.0


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
    if not sg.admin_configured():
        return JSONResponse({"detail": "admin_not_configured"}, status_code=503)
    ip = sg.client_ip(request)
    if _too_many(ip):
        return JSONResponse({"detail": "too_many_attempts"}, status_code=429, headers={"Retry-After": "300"})
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    given = str((body or {}).get("password", ""))
    if not sg.verify_admin_password(given):
        await asyncio.sleep(0.7)                       # 실패 시 지연 — 자동화된 추측 비용 증가
        return JSONResponse({"detail": "wrong_password"}, status_code=401)
    _ATTEMPTS.pop(ip, None)                            # 성공하면 실패 누적 초기화
    resp = JSONResponse({"ok": True, "expires_in": sg.ADMIN_TTL_SECONDS})
    resp.set_cookie(sg.ADMIN_COOKIE, sg.make_admin_cookie(), max_age=sg.ADMIN_TTL_SECONDS, httponly=True,
                    secure=_secure(request), samesite="lax", path="/")
    return resp


MIN_PASSWORD_LEN = 10
_ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")


def _save_hash_to_env(hashed: str) -> None:
    """.env 의 ADMIN_PASSWORD(_HASH) 줄을 지우고 해시만 기록(원자적 교체, 권한 유지). 평문은 저장하지 않는다."""
    lines = []
    if os.path.exists(_ENV_FILE):
        with open(_ENV_FILE, encoding="utf-8") as f:
            lines = [ln.rstrip("\n") for ln in f if not re.match(r"\s*(ADMIN_PASSWORD_HASH|ADMIN_PASSWORD)\s*=", ln)]
    lines.append(f"ADMIN_PASSWORD_HASH={hashed}")
    tmp = _ENV_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    if os.path.exists(_ENV_FILE):
        os.chmod(tmp, os.stat(_ENV_FILE).st_mode & 0o777)
    os.replace(tmp, _ENV_FILE)


@router.post("/setup")
async def setup(request: Request):
    """최초 비밀번호 설정 — 터미널 없이 브라우저로. 인터넷(터널) 경유 요청과 이미 설정된 서버는 거부한다."""
    if sg._via_tunnel(request):
        return JSONResponse({"detail": "setup_local_only"}, status_code=403)
    if sg.admin_configured():
        return JSONResponse({"detail": "already_configured"}, status_code=409)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    pw = str((body or {}).get("password", ""))
    if len(pw) < MIN_PASSWORD_LEN:
        return JSONResponse({"detail": "too_short", "min": MIN_PASSWORD_LEN}, status_code=422)
    hashed = sg.hash_admin_password(pw)
    _save_hash_to_env(hashed)
    os.environ["ADMIN_PASSWORD_HASH"] = hashed          # 재시작 없이 즉시 적용
    resp = JSONResponse({"ok": True, "expires_in": sg.ADMIN_TTL_SECONDS})
    resp.set_cookie(sg.ADMIN_COOKIE, sg.make_admin_cookie(), max_age=sg.ADMIN_TTL_SECONDS, httponly=True, secure=_secure(request), samesite="lax", path="/")
    return resp


@router.get("/status")
async def status(request: Request):
    configured = sg.admin_configured()
    return {"authenticated": await sg._owner_ok(request), "configured": configured, "can_setup": (not configured) and not sg._via_tunnel(request)}


@router.post("/invest-unlock")
async def invest_unlock(request: Request):
    if not sg.admin_configured():
        return JSONResponse({"detail": "admin_not_configured"}, status_code=503)
    ip = sg.client_ip(request)
    if _too_many(ip):
        return JSONResponse({"detail": "too_many_attempts"}, status_code=429, headers={"Retry-After": "300"})
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not sg.verify_admin_password(str((body or {}).get("password", ""))):
        await asyncio.sleep(0.7)
        return JSONResponse({"detail": "wrong_password"}, status_code=401)
    _ATTEMPTS.pop(ip, None)
    resp = JSONResponse({"ok": True, "expires_in": sg.INVEST_TTL_SECONDS})
    resp.set_cookie(sg.INVEST_COOKIE, sg.make_invest_cookie(), max_age=sg.INVEST_TTL_SECONDS, httponly=True, secure=_secure(request), samesite="lax", path="/")
    return resp


@router.get("/invest-status")
async def invest_status(request: Request):
    return {"unlocked": sg.valid_invest_cookie(request.cookies.get(sg.INVEST_COOKIE, ""))}


@router.post("/invest-lock")
async def invest_lock():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(sg.INVEST_COOKIE, path="/")
    return resp


@router.post("/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(sg.ADMIN_COOKIE, path="/")
    resp.delete_cookie(sg.INVEST_COOKIE, path="/")
    return resp
