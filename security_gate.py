"""security_gate.py — 터널(인터넷) 경유 요청 중 "수정" 또는 "민감 데이터 반출"에만 인증을 요구하는 게이트 (HANDOFF §11 S0 ②)

배경: stock.leanguy.cloud(Cloudflare 터널) → vite preview(5173) → uvicorn(127.0.0.1:8000) 경로에 API 인증이 없어 쓰기 엔드포인트와 보유 종목이
외부에 그대로 노출됐다. Cloudflare Access(사용자 설정)와 별개로 서버 쪽 방어선을 둔다.

정책 (2026-09-26 개정 — 사용자 요구: "일반 조회는 토큰 없이, 데이터를 수정하거나 빼가려 할 때만 인증")
  · 검사 대상 = 터널을 거친 요청(`Cf-Connecting-Ip`/`X-Forwarded-For`/`Cf-Ray` 헤더가 있음). 서버 내부·스케줄러·스크립트의 로컬 호출은 통과한다.
  · 보호 = ① /api·/hs·/semiconductor-lab 아래의 쓰기 메서드(POST/PUT/PATCH/DELETE) 전부
          ② 민감 GET: SENSITIVE_GET_PREFIXES(보유·계좌·주문·후보·연구 등)와 SENSITIVE_GET_PATTERN(download/export/backup/admin/settings 등 반출성) + API 문서
          ③ API_GATE_MODE=strict 이면 /api GET 전부(엄격 모드, 기본은 sensitive).
  · 인증 = 다음 중 하나
          (a) Cloudflare Access 로그인 JWT(`Cf-Access-Jwt-Assertion` 헤더 또는 `CF_Authorization` 쿠키) — CF_ACCESS_TEAM_DOMAIN·CF_ACCESS_AUD가 설정된 경우 서명·만료·aud·iss를
              서버가 직접 검증한다. 링크 다운로드처럼 헤더를 붙일 수 없는 브라우저 요청도 쿠키로 통과하고, 사용자는 토큰을 입력할 필요가 없다.
          (b) API 토큰: 환경변수 API_WRITE_TOKEN ↔ 헤더 `X-API-Token` 또는 `Authorization: Bearer`(상수시간 비교) — 스크립트·Access 미설정 시.
  · 인증 수단이 하나도 설정돼 있지 않으면 보호 경로의 터널 요청은 거부한다(fail-closed, 503). OPTIONS는 통과. 프런트 정적 파일은 보호 대상이 아니다.
"""
from __future__ import annotations

import base64
import hmac
import json
import os
import re
import time
import urllib.request

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
API_PREFIXES = ("/api", "/hs", "/semiconductor-lab")
DOC_PATHS = ("/docs", "/redoc", "/openapi.json")
# 개인 자산·주문·후보·운영 명령·연구 산출물 — 조회만 해도 민감하다(2026-09-25 외부 실측으로 새던 경로 포함)
SENSITIVE_GET_PREFIXES = (
    "/api/portfolio", "/api/kis-trading", "/api/commands", "/api/live-orders", "/api/research",
    "/api/realtime/prices", "/api/buy-candidates", "/api/trend/holdings", "/api/trend/trades", "/api/us-virtual",
    "/api/investment-decisions", "/api/task-approvals", "/api/watchlist", "/api/company-intelligence/portfolio",
)
# 경로 어디든 반출·관리성 세그먼트가 있으면 민감(다운로드/내보내기/백업/관리/설정/비밀)
SENSITIVE_GET_PATTERN = re.compile(r"/(download|export|backup|admin|settings?|secrets?|tokens?|credentials?)(/|$)", re.IGNORECASE)
PUBLIC_GET_PREFIXES: tuple[str, ...] = ()      # strict 모드에서도 토큰 없이 허용할 GET 접두사(현재 없음)


def gate_mode() -> str:
    return "strict" if os.environ.get("API_GATE_MODE", "sensitive").strip().lower() == "strict" else "sensitive"


def _via_tunnel(request: Request) -> bool:
    h = request.headers
    return bool(h.get("cf-connecting-ip") or h.get("x-forwarded-for") or h.get("cf-ray"))


def is_protected(method: str, path: str) -> bool:
    method = method.upper()
    if method == "OPTIONS":
        return False
    in_api = path.startswith(API_PREFIXES)
    if method in WRITE_METHODS:
        return in_api
    if path in DOC_PATHS:
        return True
    if not in_api:
        return False
    if PUBLIC_GET_PREFIXES and path.startswith(PUBLIC_GET_PREFIXES):
        return False
    if gate_mode() == "strict":
        return True
    return path.startswith(SENSITIVE_GET_PREFIXES) or bool(SENSITIVE_GET_PATTERN.search(path))


# ── 토큰 ────────────────────────────────────────────────────────────────────
def _presented_token(request: Request) -> str:
    tok = request.headers.get("x-api-token", "")
    if not tok:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            tok = auth[7:].strip()
    return tok


# ── Cloudflare Access JWT ───────────────────────────────────────────────────
_JWKS_CACHE: dict = {"at": 0.0, "team": "", "keys": {}}


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _fetch_jwks(team: str) -> dict:
    """team = 'myteam.cloudflareaccess.com'. 1시간 캐시."""
    now = time.time()
    if _JWKS_CACHE["team"] == team and now - _JWKS_CACHE["at"] < 3600 and _JWKS_CACHE["keys"]:
        return _JWKS_CACHE["keys"]
    with urllib.request.urlopen(f"https://{team}/cdn-cgi/access/certs", timeout=5) as r:   # noqa: S310 - fixed https URL from operator config
        data = json.loads(r.read().decode())
    keys = {k["kid"]: k for k in data.get("keys", []) if k.get("kty") == "RSA"}
    _JWKS_CACHE.update({"at": now, "team": team, "keys": keys})
    return keys


def verify_access_jwt(token: str, team: str, aud: str, *, keys: dict | None = None, now: float | None = None) -> dict | None:
    """Cloudflare Access가 발급한 JWT를 검증해 payload를 돌려준다(실패 시 None). 서명(RS256)·만료·aud·iss를 확인한다."""
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding, rsa

        head_b64, payload_b64, sig_b64 = token.split(".")
        header = json.loads(_b64d(head_b64))
        payload = json.loads(_b64d(payload_b64))
        if header.get("alg") != "RS256":
            return None
        jwk = (keys if keys is not None else _fetch_jwks(team)).get(header.get("kid"))
        if not jwk:
            return None
        pub = rsa.RSAPublicNumbers(int.from_bytes(_b64d(jwk["e"]), "big"), int.from_bytes(_b64d(jwk["n"]), "big")).public_key()
        pub.verify(_b64d(sig_b64), f"{head_b64}.{payload_b64}".encode(), padding.PKCS1v15(), hashes.SHA256())
        t = now if now is not None else time.time()
        if float(payload.get("exp", 0)) < t or float(payload.get("nbf", 0)) > t + 60:
            return None
        auds = payload.get("aud")
        if aud not in ([auds] if isinstance(auds, str) else (auds or [])):
            return None
        if payload.get("iss") != f"https://{team}":
            return None
        return payload
    except Exception:  # noqa: BLE001 - any parse/signature failure = not authenticated
        return None


def _access_token(request: Request) -> str:
    return request.headers.get("cf-access-jwt-assertion") or request.cookies.get("CF_Authorization", "")


async def _access_ok(request: Request) -> bool:
    team = os.environ.get("CF_ACCESS_TEAM_DOMAIN", "").strip().replace("https://", "").rstrip("/")
    aud = os.environ.get("CF_ACCESS_AUD", "").strip()
    tok = _access_token(request)
    if not (team and aud and tok):
        return False
    payload = await run_in_threadpool(verify_access_jwt, tok, team, aud)
    if not payload:
        return False
    allowed = {e.strip().lower() for e in os.environ.get("CF_ACCESS_ALLOWED_EMAILS", "").split(",") if e.strip()}
    return not allowed or str(payload.get("email", "")).lower() in allowed


async def api_token_gate(request: Request, call_next):
    if not (_via_tunnel(request) and is_protected(request.method, request.url.path)):
        return await call_next(request)
    if await _access_ok(request):                       # Cloudflare Access 로그인 사용자 — 토큰 불필요
        return await call_next(request)
    expected = os.environ.get("API_WRITE_TOKEN", "")
    access_configured = bool(os.environ.get("CF_ACCESS_TEAM_DOMAIN") and os.environ.get("CF_ACCESS_AUD"))
    if not expected and not access_configured:
        return JSONResponse({"detail": "api_token_not_configured"}, status_code=503)
    if expected and hmac.compare_digest(_presented_token(request).encode(), expected.encode()):
        return await call_next(request)
    return JSONResponse({"detail": "api_token_required"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
