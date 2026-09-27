"""
CEO Briefing Platform: 서버측 세션 토큰 발급/검증 (A05 CEO 백엔드 인증 재설계 - 1단계)

기존 문제(2026-09-12 핸드오프 A05): main.py의 거의 모든 엔드포인트가 `role: Role`을
클라이언트가 보낸 쿼리 파라미터로 그대로 받아 그 값만으로 권한을 판정했다. 로그인
(`authenticate_app_user`)은 있었지만 그 결과를 서버가 계속 기억하지 않았으므로, 로그인
이후의 모든 요청에서 role은 "서버가 인증한 값"이 아니라 "클라이언트가 주장한 값"이었다
- `?role=admin`만 붙이면 누구나 관리자 엔드포인트를 통과했다.

이 모듈은 로그인 성공 시 서명 불필요한 불투명(opaque) 토큰을 서버 메모리에 발급하고,
이후 요청은 그 토큰(Authorization: Bearer <token>)으로만 사용자/role을 해석하게 한다.
클라이언트가 보내는 role 파라미터는 이 경로에서는 아예 참조하지 않는다.

**연결 상태**: 아직 main.py/프론트엔드(kai.js, index.html)에 연결되지 않았다. 그 파일들이
이 작업 시점에 다른 세션에 의해 대량으로(수천 줄) 동시 수정되고 있어, 지금 편집하면 그
세션의 작업을 덮어쓸 위험이 있었다. 이 모듈은 그 자체로 완결적이고 테스트 가능하며, 실제
연결(기존 role 파라미터 제거, 엔드포인트에 Depends(require_*) 적용, 프론트엔드가 토큰을
저장·전송하도록 수정)은 해당 세션이 안정된 뒤 별도 작업으로 진행한다.
"""

import os
import time
import secrets
from pathlib import Path
from typing import Any, Dict, Optional


def _configured_secret() -> Optional[str]:
    value = os.getenv("CEO_SESSION_SECRET")
    if value:
        return value
    env_file = Path(__file__).resolve().parent.parent / ".env"
    try:
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, candidate = line.split("=", 1)
                if key.strip() == "CEO_SESSION_SECRET":
                    return candidate.strip().strip('"').strip("'") or None
    except OSError:
        pass
    return None


def _default_ttl_seconds() -> int:
    try:
        return int(os.getenv("CEO_SESSION_TTL_SECONDS", str(12 * 3600)))
    except ValueError:
        return 12 * 3600


class SessionStore:
    """세션 토큰 저장소. 기본은 프로세스 메모리 - 재시작하면 재로그인이 필요해지는 정도의
    손실은 감내 가능하다고 보고 우선 이 형태로 시작한다. 여러 프로세스/워커로 확장하거나
    재시작 후에도 세션을 유지해야 하면 antigravity_workspace/memory/state_ledger.py와
    같은 영속 저장소로 옮긴다(이번 모듈은 그 결정을 미리 내리지 않는다)."""

    def __init__(self, secret: Optional[str] = None, ttl_seconds: Optional[int] = None):
        self.secret = secret if secret is not None else _configured_secret()
        self.ttl_seconds = ttl_seconds if ttl_seconds is not None else _default_ttl_seconds()
        self._sessions: Dict[str, Dict[str, Any]] = {}

    def _require_secret(self) -> None:
        # secret 자체를 토큰 생성에 쓰지는 않지만(불투명 랜덤 토큰 방식), 운영자가 이
        # 기능을 의도적으로 활성화했다는 최소한의 확인 게이트로 사용한다. 값이 없으면
        # 세션 발급/조회를 전부 막아 "설정 안 했는데 열려있는" 상태를 방지한다(fail-closed).
        if not self.secret:
            raise RuntimeError(
                "CEO_SESSION_SECRET이 설정되지 않았습니다. .env에 값을 설정하기 전에는 "
                "세션 토큰 발급/검증을 사용할 수 없습니다."
            )

    def issue(self, username: str, role: str, display_name: str) -> Dict[str, Any]:
        """로그인 성공 후 호출한다. 새 토큰과 만료시각을 반환한다."""
        self._require_secret()
        token = secrets.token_urlsafe(32)
        now = time.time()
        session = {
            "username": username,
            "role": role,
            "display_name": display_name,
            "issued_at": now,
            "expires_at": now + self.ttl_seconds,
        }
        self._sessions[token] = session
        return {"token": token, "expires_at": session["expires_at"]}

    def resolve(self, token: Optional[str]) -> Optional[Dict[str, Any]]:
        """토큰으로 세션을 조회한다. 없거나 만료됐으면 None (호출자가 401 처리)."""
        if not token:
            return None
        session = self._sessions.get(token)
        if not session:
            return None
        if session["expires_at"] < time.time():
            del self._sessions[token]
            return None
        return session

    def revoke(self, token: str) -> bool:
        return self._sessions.pop(token, None) is not None

    def revoke_all_for_user(self, username: str) -> int:
        """계정 삭제/역할 변경 시 그 사용자의 기존 세션을 전부 무효화한다."""
        to_delete = [t for t, s in self._sessions.items() if s["username"] == username]
        for t in to_delete:
            del self._sessions[t]
        return len(to_delete)

    def active_session_count(self) -> int:
        now = time.time()
        return sum(1 for s in self._sessions.values() if s["expires_at"] >= now)


# 프로세스 전역 기본 인스턴스 - main.py에 연결할 때 이걸 import해서 쓴다.
default_store = SessionStore()
