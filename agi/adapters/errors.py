"""AGI 어댑터 공통 오류 타입.

`FetchPolicyError` 는 **"가져오지 않기로 한 결정"** 을 나타낸다 — 네트워크 장애(5xx·timeout)나
HTTP 상태(403/404)와 구분해야 한다. 이유: 정책 차단은 **설정 오류일 수 있으므로** 문서에
`unavailable` 로 남기되 실행 요약에 사유 코드를 노출해야 하고, 상태 코드는 서버가 준 사실이다.
둘을 같은 값으로 뭉개면 "차단 0건"이 "정책이 맞다"는 증거로 오독된다.
"""

from __future__ import annotations

# 정책 차단 사유 코드(자유문장 금지 — 집계·테스트 가능해야 한다).
CODE_SCHEME_NOT_ALLOWED = "scheme_not_allowed"
CODE_HOST_NOT_ALLOWED = "host_not_allowed"
CODE_PORT_NOT_ALLOWED = "port_not_allowed"
CODE_USERINFO_NOT_ALLOWED = "userinfo_not_allowed"
CODE_URL_MALFORMED = "url_malformed"
CODE_URL_TOO_LONG = "url_too_long"
CODE_ADDRESS_NOT_ALLOWED = "address_not_allowed"
CODE_RESOLVE_FAILED = "resolve_failed"
CODE_REDIRECT_BLOCKED = "redirect_blocked"
CODE_REDIRECT_TARGET_NOT_ALLOWED = "redirect_target_not_allowed"
CODE_CONTENT_TYPE_NOT_ALLOWED = "content_type_not_allowed"
CODE_RESPONSE_TOO_LARGE = "response_too_large"
CODE_TIMEOUT = "timeout"
CODE_TRANSPORT_ERROR = "transport_error"
CODE_UNEXPECTED_STATUS = "unexpected_status"
# 수집 정책 계층(robots) — 수집 정책이 막은 것도 "가져오지 않기로 한 결정"이다.
CODE_ROBOTS_DISALLOWED = "robots_disallowed"
CODE_ROBOTS_UNAVAILABLE = "robots_unavailable"


class FetchPolicyError(Exception):
    """URL·응답이 정책을 위반해 **본문을 가져오지 않았다**는 뜻(값이 아니라 결정)."""

    def __init__(self, code: str, message: str, *, url: str | None = None, status: int | None = None):
        self.code = code
        self.url = url
        self.status = status
        super().__init__(f"{code}: {message}")

    def reason(self) -> str:
        """문서 tombstone 사유 코드(`policy_blocked:<code>`) — 자유문장을 넣지 않는다."""
        return f"policy_blocked:{self.code}"
