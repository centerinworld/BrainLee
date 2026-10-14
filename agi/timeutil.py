"""시간 4종 변환 — 계약 §4(AGI-01)의 **단일 변환 지점**.

기존 테이블의 시각 컬럼은 TEXT 이고 포맷이 2종 섞여 있다(AGI-01 실측 M2):

    telegram_channel_posts.msg_date      = '2026-10-10 03:41:30'   (공백 구분)
    telegram_channel_posts.collected_at  = '2026-10-11T08:35:19'   (ISO 'T')

둘 다 오프셋이 없고 KST 로 수집됐다. TEXT 사전식 정렬은 실제 순서와 다르므로
(§4-3) 신설 테이블에는 `timestamptz` 만 저장하고, 해석은 **이 파일에서만** 한다.

주의: 여기서 KST 를 가정하는 것은 **레거시 TEXT 입력에 대한 명시적 해석**이며,
새로 수집하는 값에 오프셋 없이 KST 를 가정하는 근거가 아니다(§4-1).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

# 계약 §4-1: 저장은 UTC(timestamptz), 표시는 KST.
KST = timezone(timedelta(hours=9), "KST")
UTC = timezone.utc

# 레거시 TEXT 입력이 실제로 보이는 포맷들(M2 + 초 단위 절단 변형).
_NAIVE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d",
)

_MYSQL_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def parse_legacy_kst(value) -> datetime | None:
    """레거시 TEXT 시각을 KST 로 해석해 **UTC aware datetime** 으로 돌려준다.

    해석 불가면 `None` — 추정값을 만들지 않는다(§4-5: 근거 없는 값은 NULL 유지).
    이미 aware datetime 이면 그대로 UTC 로 변환한다.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=KST).astimezone(UTC)
    text = str(value).strip()
    if not text:
        return None
    # 오프셋이 명시된 입력은 그 오프셋을 신뢰한다.
    try:
        explicit = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        explicit = None
    if explicit is not None and explicit.tzinfo is not None:
        return explicit.astimezone(UTC)
    for fmt in _NAIVE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=KST).astimezone(UTC)
        except ValueError:
            continue
    return None


def to_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=KST).astimezone(UTC)


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def to_kst_text(value: datetime | None) -> str | None:
    """화면/응답용 KST 문자열(계약 §4-1). 저장에는 쓰지 않는다."""
    if value is None:
        return None
    return to_utc(value).astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")
