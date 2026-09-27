"""role_gate.py — 터널(인터넷) 경유 요청은 로그인 세션 없이는 통과시키지 않고, `role`은 항상 서버 세션에서 결정한다 (2026-09-26)

문제(2026-09-12 핸드오프 A05, 2026-09-26 실측): main.py의 거의 모든 엔드포인트가 `role: Role`(admin/ceo/staff)을 **클라이언트가 보낸 쿼리 파라미터**로 받아 그 값만으로 권한을 판정했다.
`?role=admin`만 붙이면 누구나 텔레그램 봇 토큰 교체·사용자 생성/삭제·OpenAI 설정 변경 등을 할 수 있었고, Access를 걷어낸 뒤 api.newsinfo.cloud는 인터넷에 그대로 열려 있었다.

이 게이트(순수 ASGI 미들웨어)는 엔드포인트 수정 없이 근본 원인을 없앤다:
  · 터널 경유 요청(`Cf-Connecting-Ip`/`X-Forwarded-For`/`Cf-Ray`)만 검사한다. 서버 내부·로컬 관리 콘솔·스크립트의 직접 호출은 기존처럼 동작한다.
  · PUBLIC_PATHS(/health, /app-login, /agentic-code 페이지)를 뺀 모든 요청은 `Authorization: Bearer <세션 토큰>`이 유효해야 한다(아이디+PIN 로그인으로 발급). 없으면 401 `login_required`.
  · 유효한 세션이면 쿼리의 `role` 파라미터를 **세션의 role로 덮어쓴다**(클라이언트가 보낸 값은 버린다). 이후 `require_admin(role)` 등 기존 판정이 서버가 인증한 값을 보게 된다.
  · /app-login 무차별 대입 방지: IP당 10분에 10회.
"""
from __future__ import annotations

import json
import re
import time
from collections import defaultdict, deque
from typing import Callable, Optional
from urllib.parse import parse_qsl, urlencode

PUBLIC_PATHS = ("/health", "/app-login", "/agentic-code")
# 2026-09-27: Key Indicator(주요 경제지표·뉴스정보)는 누구나 볼 수 있다 — 로그인 없는 **GET 조회**만, 아래 목록의 경로에 한해 role=staff(읽기 전용)로 통과시킨다.
# 쓰기(POST/PUT/DELETE)·설정·토큰·사용자·관리 화면 API는 여기에 없으므로 계속 관리자 로그인이 필요하다.
PUBLIC_READ = re.compile(
    r"^/(api/eco/(indicators|categories)(/[A-Za-z0-9_\-]+/history)?"
    r"|api/global-macro/(latest|dashboard|categories|stats|timeseries/[A-Za-z0-9_\-]+|events|events/(surprises|reactions)|commodities|commodities/correlations|insights|insights/(regime|lead-lag))"
    r"|feeds/company)/?$"
)
LOGIN_ATTEMPTS = 10
LOGIN_WINDOW = 600.0
_ATTEMPTS: dict[str, deque] = defaultdict(deque)
TUNNEL_HEADERS = (b"cf-connecting-ip", b"x-forwarded-for", b"cf-ray")


def _too_many_logins(ip: str, now: Optional[float] = None) -> bool:
    t = now if now is not None else time.time()
    q = _ATTEMPTS[ip]
    while q and q[0] <= t - LOGIN_WINDOW:
        q.popleft()
    if len(q) >= LOGIN_ATTEMPTS:
        return True
    q.append(t)
    return False


class RoleGate:
    def __init__(self, app, store=None, public_paths=PUBLIC_PATHS):
        self.app = app
        if store is None:
            from session_auth import default_store
            store = default_store
        self.store = store
        self.public_paths = tuple(public_paths)

    @staticmethod
    def _headers(scope) -> dict:
        return {k.lower(): v for k, v in scope.get("headers", [])}

    async def _respond(self, send, status: int, detail: str, extra: Optional[list] = None) -> None:
        body = json.dumps({"detail": detail}).encode()
        headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())] + (extra or [])
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = self._headers(scope)
        if not any(h in headers for h in TUNNEL_HEADERS):          # 로컬·내부 호출은 그대로
            return await self.app(scope, receive, send)
        path, method = scope.get("path", ""), scope.get("method", "GET").upper()
        if method == "OPTIONS":
            return await self.app(scope, receive, send)
        if path == "/app-login" and method == "POST":
            ip = (headers.get(b"cf-connecting-ip") or headers.get(b"x-forwarded-for", b"unknown").split(b",")[0]).decode().strip()
            if _too_many_logins(ip):
                return await self._respond(send, 429, "too_many_login_attempts", [(b"retry-after", b"300")])
        if any(path == p or path.startswith(p + "/") for p in self.public_paths):
            return await self.app(scope, receive, send)
        auth = headers.get(b"authorization", b"").decode()
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else None
        session = self.store.resolve(token) if token else None
        if not session:
            if method == "GET" and PUBLIC_READ.match(path):       # 공개 조회: 읽기 전용 role 로 통과
                pairs = [(k, v) for k, v in parse_qsl(scope.get("query_string", b"").decode(), keep_blank_values=True) if k != "role"]
                pairs.append(("role", "staff"))
                scope = dict(scope)
                scope["query_string"] = urlencode(pairs).encode()
                return await self.app(scope, receive, send)
            return await self._respond(send, 401, "login_required", [(b"www-authenticate", b"Bearer")])
        # 세션의 role로 덮어쓴다 — 클라이언트가 보낸 role 값은 버린다
        pairs = [(k, v) for k, v in parse_qsl(scope.get("query_string", b"").decode(), keep_blank_values=True) if k != "role"]
        pairs.append(("role", session["role"]))
        scope = dict(scope)
        scope["query_string"] = urlencode(pairs).encode()
        scope["state"] = {**scope.get("state", {}), "session": session}
        return await self.app(scope, receive, send)
