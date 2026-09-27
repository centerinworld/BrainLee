"""security_gate.py — 터널(인터넷) 경유 요청 게이트: 기본은 읽기 전용 공개, 수정·반출은 관리자, 계좌현황은 서버 검증 비밀번호 (HANDOFF §11 S0)

배경: 이 사이트는 친구·외부인이 별도 로그인 없이 화면을 본다. 그러나 서버 API에는 인증이 없어 쓰기 엔드포인트(124개)와 실보유 종목이 인터넷에 그대로 노출됐다.
계좌현황 탭의 비밀번호도 브라우저 코드에서만 비교해 API를 직접 부르면 보호가 없었다. (2026-09-26 사용자 요구: "기본은 읽기 전용, 수정·반출만 막는다")

접근 단계 (터널 = Cloudflare가 붙이는 `Cf-Connecting-Ip`/`X-Forwarded-For`/`Cf-Ray` 헤더가 있는 요청만 검사, 서버 내부·스케줄러·스크립트의 로컬 호출은 무영향)
  · public : 일반 조회(시세·랭킹·차트·전략 화면 등) — 인증 없음. 대량 수집 방지용으로 IP당 분당 요청 수만 제한(API_RATE_LIMIT_PER_MIN, 기본 300, 0=끔).
  · viewer : 「내 투자」 GET(VIEWER_GET_PREFIXES: 보유·거래내역·매수후보·보유종목 시세·실계좌 요약·현금원장) — 2026-09-27부터 **관리자 로그인만** 통과(별도 열람 비밀번호·pf_view 쿠키 폐지).
             (로그인은 POST /api/portfolio-access/login. 친구는 지금처럼 비밀번호만 입력하면 되고 토큰은 묻지 않는다.)
  · public write: 일반 사용자 기능인 안전한 쓰기(종목 검색·분석 요청, 텍스트 파싱)는 PUBLIC_WRITE_PATTERNS 허용 목록에만 열고 IP당 분당 30회로 제한한다
             (환경변수 API_PUBLIC_WRITE_PATTERNS=쉼표 구분 정규식으로 조정). 나머지 쓰기는 전부 owner.
  · owner  : 수정(POST/PUT/PATCH/DELETE) 전부(위 허용 목록 제외), 내보내기·다운로드·백업·관리·설정 성격의 GET, API 문서 — 관리자만.
             관리자 자격 = 관리자 로그인 세션 쿠키(`sd_admin`, POST /api/admin-auth/login 이 서버에서 비밀번호를 확인한 뒤 발급 — 2026-09-27) 또는
             API 토큰(`X-API-Token`/`Authorization: Bearer` ↔ API_WRITE_TOKEN) 또는 Cloudflare Access JWT(서명·만료·aud·iss 검증,
             email 클레임이 있고 CF_ACCESS_ALLOWED_EMAILS(관리자 이메일 목록, 필수)에 든 경우 — Access에 친구를 허용해도 관리자로 취급되지 않는다).
  · API_GATE_MODE=strict 이면 모든 /api GET을 owner로 취급(엄격 모드, 기본은 sensitive).
인증 수단이 설정돼 있지 않으면 해당 단계의 터널 요청은 거부한다(fail-closed, 503). OPTIONS·프런트 정적 파일·/api/portfolio-access/*는 검사하지 않는다.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
import urllib.request
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
API_PREFIXES = ("/api", "/hs", "/semiconductor-lab")
DOC_PATHS = ("/docs", "/redoc", "/openapi.json")
PUBLIC_PREFIXES = ("/api/portfolio-access/", "/api/admin-auth/")   # 열람·관리자 로그인/상태 — 자체 검증
# 계좌현황(소유자의 실제 보유·거래·실계좌) — 서버 검증 비밀번호 쿠키 필요. 그 밖의 화면(관심종목·수집 상태·가상매매(paper)·리스크게이트 등)은 모두 공개.
VIEWER_GET_PREFIXES = ("/api/portfolio", "/api/buy-candidates", "/api/realtime/prices", "/api/kis-trading/account", "/api/kis-trading/cash-ledger", "/api/live-orders")
# 반출·관리성 세그먼트가 있는 GET — 관리자만
OWNER_GET_PREFIXES = ("/api/sysmap",)                     # 시스템 지도(내부 구조 노출) — 관리자 전용
OWNER_GET_PATTERN = re.compile(r"/(download|export|backup|admin|settings?|secrets?|tokens?|credentials?)(/|$)", re.IGNORECASE)
# 일반 사용자가 쓰는 안전한 쓰기(저장·삭제·외부 호출 비용이 거의 없는 요청) — 그 외 쓰기는 모두 관리자 전용
DEFAULT_PUBLIC_WRITE_PATTERNS = (r"^/api/commands/analyze/[^/]+$", r"^/api/sector-define/parse$")
VIEW_COOKIE = "pf_view"
VIEW_TTL_SECONDS = 12 * 3600
ADMIN_COOKIE = "sd_admin"
ADMIN_TTL_SECONDS = 8 * 3600
INVEST_COOKIE = "sd_invest"            # 「내 투자」 잠금 해제 — 관리자 로그인과 별개로 비밀번호를 다시 입력해야 발급(2026-09-27)
INVEST_TTL_SECONDS = 30 * 60


def public_write_patterns() -> list:
    raw = os.environ.get("API_PUBLIC_WRITE_PATTERNS")
    pats = [x.strip() for x in raw.split(",") if x.strip()] if raw is not None else list(DEFAULT_PUBLIC_WRITE_PATTERNS)
    return [re.compile(x) for x in pats]


def is_public_write(method: str, path: str) -> bool:
    return method.upper() in WRITE_METHODS and any(p.search(path) for p in public_write_patterns())


def gate_mode() -> str:
    return "strict" if os.environ.get("API_GATE_MODE", "sensitive").strip().lower() == "strict" else "sensitive"


def _via_tunnel(request: Request) -> bool:
    h = request.headers
    return bool(h.get("cf-connecting-ip") or h.get("x-forwarded-for") or h.get("cf-ray"))


def access_level(method: str, path: str) -> str:
    """'public' | 'viewer' | 'owner'"""
    method = method.upper()
    if method == "OPTIONS" or path.startswith(PUBLIC_PREFIXES):
        return "public"
    in_api = path.startswith(API_PREFIXES)
    if method in WRITE_METHODS:
        if not in_api or is_public_write(method, path):
            return "public"
        return "owner"
    if path in DOC_PATHS:
        return "owner"
    if not in_api:
        return "public"
    if gate_mode() == "strict" or OWNER_GET_PATTERN.search(path) or path.startswith(OWNER_GET_PREFIXES):
        return "owner"
    if path.startswith(VIEWER_GET_PREFIXES):
        return "viewer"
    return "public"


def is_protected(method: str, path: str) -> bool:
    return access_level(method, path) != "public"


# ── 관리자 토큰 ─────────────────────────────────────────────────────────────
def _presented_token(request: Request) -> str:
    tok = request.headers.get("x-api-token", "")
    if not tok:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            tok = auth[7:].strip()
    return tok


# ── 열람(계좌현황) 쿠키: 서버가 비밀번호를 확인한 뒤 발급하는 서명 쿠키 ────────────────
def _view_secret() -> bytes:
    explicit = os.environ.get("VIEW_COOKIE_SECRET", "")
    if explicit:
        return explicit.encode()
    token = os.environ.get("API_WRITE_TOKEN", "")
    return hashlib.sha256(("pf_view:" + token).encode()).digest() if token else b""


def make_view_cookie(now: float | None = None) -> str:
    secret = _view_secret()
    if not secret:
        raise RuntimeError("view_secret_not_configured")
    exp = int((now if now is not None else time.time()) + VIEW_TTL_SECONDS)
    return f"{exp}.{hmac.new(secret, str(exp).encode(), hashlib.sha256).hexdigest()}"


def valid_view_cookie(value: str, now: float | None = None) -> bool:
    secret = _view_secret()
    try:
        exp_s, sig = (value or "").split(".", 1)
        if not secret or int(exp_s) < (now if now is not None else time.time()):
            return False
        return hmac.compare_digest(sig, hmac.new(secret, exp_s.encode(), hashlib.sha256).hexdigest())
    except Exception:  # noqa: BLE001
        return False


# ── 관리자 로그인(비밀번호 → 서명 세션 쿠키) ─────────────────────────────────────────
def admin_configured() -> bool:
    return bool(os.environ.get("ADMIN_PASSWORD_HASH") or os.environ.get("ADMIN_PASSWORD"))


def hash_admin_password(password: str, *, iterations: int = 600_000, salt: bytes | None = None) -> str:
    """`pbkdf2_sha256$반복$salt(hex)$hash(hex)` — .env의 ADMIN_PASSWORD_HASH에 넣는 형식(scripts/ops/set_admin_password.py)."""
    salt = salt if salt is not None else os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def verify_admin_password(given: str) -> bool:
    """ADMIN_PASSWORD_HASH(권장) 또는 ADMIN_PASSWORD(평문 — 임시용)와 상수시간 비교. 둘 다 없으면 항상 False(fail-closed)."""
    stored = os.environ.get("ADMIN_PASSWORD_HASH", "")
    if stored:
        try:
            algo, iters, salt_hex, hash_hex = stored.split("$")
            if algo != "pbkdf2_sha256":
                return False
            dk = hashlib.pbkdf2_hmac("sha256", given.encode(), bytes.fromhex(salt_hex), int(iters))
            return hmac.compare_digest(dk.hex(), hash_hex)
        except Exception:  # noqa: BLE001 - 깨진 해시 = 인증 실패
            return False
    plain = os.environ.get("ADMIN_PASSWORD", "")
    return bool(plain) and hmac.compare_digest(given.encode(), plain.encode())


def _admin_secret() -> bytes:
    """세션 서명 키. 비밀번호(해시)를 바꾸면 기존 세션이 모두 무효가 되도록 비밀번호 자료에서 파생한다."""
    explicit = os.environ.get("ADMIN_COOKIE_SECRET", "")
    base = explicit or os.environ.get("ADMIN_PASSWORD_HASH") or os.environ.get("ADMIN_PASSWORD") or ""
    return hashlib.sha256(("sd_admin:" + base + os.environ.get("API_WRITE_TOKEN", "")).encode()).digest() if base else b""


def make_admin_cookie(now: float | None = None) -> str:
    secret = _admin_secret()
    if not secret:
        raise RuntimeError("admin_not_configured")
    exp = int((now if now is not None else time.time()) + ADMIN_TTL_SECONDS)
    return f"{exp}.{hmac.new(secret, f'admin:{exp}'.encode(), hashlib.sha256).hexdigest()}"


def valid_admin_cookie(value: str, now: float | None = None) -> bool:
    secret = _admin_secret()
    try:
        exp_s, sig = (value or "").split(".", 1)
        if not secret or int(exp_s) < (now if now is not None else time.time()):
            return False
        return hmac.compare_digest(sig, hmac.new(secret, f"admin:{exp_s}".encode(), hashlib.sha256).hexdigest())
    except Exception:  # noqa: BLE001
        return False



def make_invest_cookie(now: float | None = None) -> str:
    secret = _admin_secret()
    if not secret:
        raise RuntimeError("admin_not_configured")
    exp = int((now if now is not None else time.time()) + INVEST_TTL_SECONDS)
    return f"{exp}.{hmac.new(secret, f'invest:{exp}'.encode(), hashlib.sha256).hexdigest()}"


def valid_invest_cookie(value: str, now: float | None = None) -> bool:
    secret = _admin_secret()
    try:
        exp_s, sig = (value or "").split(".", 1)
        if not secret or int(exp_s) < (now if now is not None else time.time()):
            return False
        return hmac.compare_digest(sig, hmac.new(secret, f"invest:{exp_s}".encode(), hashlib.sha256).hexdigest())
    except Exception:  # noqa: BLE001
        return False


# ── 요청 수 제한(대량 수집 방지) ────────────────────────────────────────────────
_HITS: dict[str, deque] = defaultdict(deque)
_LAST_PRUNE = [0.0]


def client_ip(request: Request) -> str:
    h = request.headers
    return (h.get("cf-connecting-ip") or (h.get("x-forwarded-for") or "").split(",")[0].strip() or "unknown")


def rate_limited(ip: str, limit: int, now: float | None = None, window: float = 60.0) -> bool:
    if limit <= 0:
        return False
    t = now if now is not None else time.time()
    q = _HITS[ip]
    while q and q[0] <= t - window:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(t)
    if t - _LAST_PRUNE[0] > 300:                           # 오래된 IP 항목 정리
        _LAST_PRUNE[0] = t
        for k in [k for k, v in _HITS.items() if not v or v[-1] <= t - window]:
            _HITS.pop(k, None)
    return False


# ── Cloudflare Access JWT (관리자 인증 수단 중 하나) ──────────────────────────────
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
    """Cloudflare Access가 발급한 JWT를 검증해 payload를 돌려준다(실패 시 None). 서명(RS256)·만료·aud·iss를 확인하고, 로그인한 사용자의 email 클레임이 있어야 한다."""
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
        # 로그인한 사람의 토큰만 인정: Access가 Bypass/익명 방문자에게 발급하는 앱 토큰(type='app', email 없음)은 서명이 유효해도 거부(2026-09-26 실측)
        if payload.get("type") == "app" or not str(payload.get("email", "")).strip():
            return None
        return payload
    except Exception:  # noqa: BLE001 - any parse/signature failure = not authenticated
        return None


def _access_token(request: Request) -> str:
    return request.headers.get("cf-access-jwt-assertion") or request.cookies.get("CF_Authorization", "")


async def _access_owner_ok(request: Request) -> bool:
    team = os.environ.get("CF_ACCESS_TEAM_DOMAIN", "").strip().replace("https://", "").rstrip("/")
    aud = os.environ.get("CF_ACCESS_AUD", "").strip()
    owners = {e.strip().lower() for e in os.environ.get("CF_ACCESS_ALLOWED_EMAILS", "").split(",") if e.strip()}
    tok = _access_token(request)
    if not (team and aud and owners and tok):          # 관리자 이메일 목록이 없으면 Access 로그인으로는 관리자 권한을 주지 않는다
        return False
    payload = await run_in_threadpool(verify_access_jwt, tok, team, aud)
    return bool(payload) and str(payload.get("email", "")).lower() in owners


async def _owner_ok(request: Request, *, allow_admin_cookie: bool = True) -> bool:
    if allow_admin_cookie and valid_admin_cookie(request.cookies.get(ADMIN_COOKIE, "")):
        return True
    expected = os.environ.get("API_WRITE_TOKEN", "")
    if expected and hmac.compare_digest(_presented_token(request).encode(), expected.encode()):
        return True
    return await _access_owner_ok(request)


def _owner_configured() -> bool:
    return bool(os.environ.get("API_WRITE_TOKEN") or admin_configured() or (os.environ.get("CF_ACCESS_ALLOWED_EMAILS") and os.environ.get("CF_ACCESS_TEAM_DOMAIN")
                                                     and os.environ.get("CF_ACCESS_AUD")))


async def api_token_gate(request: Request, call_next):
    if not _via_tunnel(request):
        return await call_next(request)
    path, method = request.url.path, request.method.upper()
    if method in ("GET", "HEAD") and path.startswith(API_PREFIXES):
        limit = int(os.environ.get("API_RATE_LIMIT_PER_MIN", "300") or 0)
        if rate_limited(client_ip(request), limit):
            return JSONResponse({"detail": "rate_limited"}, status_code=429, headers={"Retry-After": "30"})
    level = access_level(method, path)
    if level == "public":
        if is_public_write(method, path) and rate_limited("w:" + client_ip(request), int(os.environ.get("API_PUBLIC_WRITE_LIMIT_PER_MIN", "30") or 0)):
            return JSONResponse({"detail": "rate_limited"}, status_code=429, headers={"Retry-After": "30"})
        return await call_next(request)
    if level == "viewer":
        # 2026-09-27: 「내 투자」(계좌현황·매수후보)는 관리자 로그인 상태여도 **비밀번호를 다시 입력해 발급한 잠금 해제 쿠키(sd_invest, 30분)** 가 있어야 한다.
        # 스크립트용 API 토큰·Cloudflare Access 관리자는 기존처럼 통과.
        if not admin_configured():
            return JSONResponse({"detail": "admin_not_configured"}, status_code=503)
        if valid_invest_cookie(request.cookies.get(INVEST_COOKIE, "")) or await _owner_ok(request, allow_admin_cookie=False):
            return await call_next(request)
        return JSONResponse({"detail": "invest_unlock_required"}, status_code=401)
    if await _owner_ok(request):                       # 관리자는 나머지 단계 통과
        return await call_next(request)
    if not _owner_configured():
        return JSONResponse({"detail": "api_token_not_configured"}, status_code=503)
    return JSONResponse({"detail": "api_token_required"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
