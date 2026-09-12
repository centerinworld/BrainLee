"""
CEO Briefing Platform: FastAPI 의존성 래퍼 (session_auth.py를 엔드포인트에 연결할 때 사용)

**아직 main.py에서 사용되지 않는다** - session_auth.py 상단 docstring 참조. main.py가
안정된 뒤, 각 엔드포인트의 `role: Role` 파라미터를 아래 `require_any_session` 또는
`require_admin_session`으로 교체하는 작업(및 role_has_page_access류 페이지 권한 검사를
세션에서 나온 role로 다시 연결하는 작업)이 남아 있다.
"""

from typing import Any, Dict, Optional

from fastapi import Depends, Header, HTTPException

from session_auth import default_store


def _extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def require_any_session(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    """로그인된 사용자면 통과. role은 세션에서만 가져온다 - 요청의 role 파라미터는 무시한다."""
    token = _extract_bearer_token(authorization)
    session = default_store.resolve(token)
    if not session:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다 (세션 없음 또는 만료)")
    return session


def require_admin_session(session: Dict[str, Any] = Depends(require_any_session)) -> Dict[str, Any]:
    if session["role"] != "admin":
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다")
    return session
