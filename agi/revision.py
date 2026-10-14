"""AGI 원문 revision 해시 정규 알고리즘.

정본: `docs/AGI_01_SOURCE_TIME_AUTH_CONTRACT_20261011.md` §5-1.
이 파일은 그 의사코드를 **그대로** 옮긴 것이며, 계약을 바꾸지 않고 여기만 고치지 않는다.

    text_norm   = NFC(개행 \\n 통일) → 연속 공백 정리 → trim
    text_digest = sha256(utf8(text_norm))

    attachment_key    = 문서 루트 기준 상대경로, '/' 구분, NFC, 선행 './' 제거
                        (경로가 없는 첨부는 f"__nopath__/{index:04d}")
    attachment_digest = sha256(바이트 원문)          # 재인코딩·재압축 금지

    entry(i) = attachment_key + "\\x1f" + attachment_digest
    entries  = sorted(...)   # (attachment_key, attachment_digest) 바이트 오름차순,
                             # 대소문자 구분, 로케일 비의존
    entries  = dedupe(...)   # 동일 (key,digest) 중복은 1건으로 축약

    revision_hash = sha256(utf8("v1\\n" + text_digest + "\\n" + "\\n".join(entries)))
                    # 첨부 0건이면 join 결과는 빈 문자열(구분자·플레이스홀더 금지)

불변식(수용기준 T14):
  * 첨부를 **역순·무작위 순서**로 넣어도 해시는 같다(정렬 규칙 1개만 존재).
  * 내용이 같은 파일의 **경로 변경은 새 revision**이다(키가 해시에 들어간다).
  * 접두 `v1\\n` 은 알고리즘 버전이다. 접두가 다른 해시를 같은 revision 으로 취급하지 않는다.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Iterable, Sequence

ALGORITHM_VERSION = "v1"

ENTRY_SEPARATOR = "\x1f"
ENTRY_JOIN = "\n"

CHANGE_KINDS = (
    "new",
    "text_edit",
    "attachment_added",
    "attachment_removed",
    "attachment_renamed",
    "mixed",
)

# 첨부는 (key, data) 쌍으로 받는다. key=None 이면 경로 없는 첨부다.
Attachment = tuple[str | None, bytes | str]


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _as_bytes(data: bytes | str) -> bytes:
    if isinstance(data, bytes):
        return data
    if isinstance(data, bytearray):
        return bytes(data)
    return str(data).encode("utf-8")


def normalize_text(text: str | None) -> str:
    """본문 정규화 — NFC → 개행 통일 → 연속 공백 정리 → trim."""
    if text is None:
        return ""
    value = unicodedata.normalize("NFC", str(text))
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    # 연속 공백 정리: 줄 단위로 선두/후미/중복 공백을 하나로 줄인다.
    normalized_lines = [" ".join(line.split()) for line in value.split("\n")]
    return "\n".join(normalized_lines).strip()


def text_digest(text: str | None) -> str:
    return _sha256_hex(normalize_text(text).encode("utf-8"))


def normalize_attachment_key(key: str | None, index: int) -> str:
    """문서 루트 기준 상대경로로 정규화. 경로가 없으면 `__nopath__/{index:04d}`."""
    if key is None or str(key).strip() == "":
        return f"__nopath__/{index:04d}"
    value = unicodedata.normalize("NFC", str(key)).replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    value = value.lstrip("/")
    return value or f"__nopath__/{index:04d}"


def attachment_entry(key: str | None, data: bytes | str, index: int = 1) -> str:
    """`attachment_key \\x1f attachment_digest` 한 줄."""
    return normalize_attachment_key(key, index) + ENTRY_SEPARATOR + _sha256_hex(_as_bytes(data))


def _entry_key(entry: str) -> str:
    return entry.split(ENTRY_SEPARATOR, 1)[0]


def _entry_digest(entry: str) -> str:
    parts = entry.split(ENTRY_SEPARATOR, 1)
    return parts[1] if len(parts) == 2 else ""


def canonical_entries(attachments: Iterable[Attachment] = ()) -> list[str]:
    """정렬 + 중복축약된 첨부 entry 목록(결정적).

    정렬 키는 `(attachment_key, attachment_digest)` 의 **바이트** 오름차순이다.
    파일시스템 열거 순서·DB 반환 순서·로케일에 의존하지 않는다.
    """
    entries: list[str] = []
    for index, attachment in enumerate(attachments, start=1):
        key, data = attachment
        entries.append(attachment_entry(key, data, index))
    # 동일 (key,digest) 중복은 1건으로 축약한다.
    unique = list(dict.fromkeys(entries))
    unique.sort(key=lambda entry: (_entry_key(entry).encode("utf-8"), _entry_digest(entry).encode("utf-8")))
    return unique


def compute_revision_hash(
    text: str | None,
    attachments: Iterable[Attachment] = (),
) -> tuple[str, str, list[str]]:
    """(revision_hash, text_digest, canonical_entries) 를 돌려준다."""
    digest = text_digest(text)
    entries = canonical_entries(attachments)
    payload = ALGORITHM_VERSION + "\n" + digest + "\n" + ENTRY_JOIN.join(entries)
    return _sha256_hex(payload.encode("utf-8")), digest, entries


def canonical_attachment_string(entries: Sequence[str]) -> str:
    """첨부 entry 목록을 DB 1급 컬럼(`attachments_canonical`) 문자열로 만든다.

    해시가 계산된 바로 그 문자열이므로, 저장된 값으로 해시를 재계산해 검증할 수 있다.
    """
    return ENTRY_JOIN.join(entries)


def digests_by_key(entries: Iterable[str]) -> dict[str, str]:
    return {_entry_key(entry): _entry_digest(entry) for entry in entries}


def classify_change(
    previous: tuple[str, Sequence[str]] | None,
    current: tuple[str, Sequence[str]],
) -> str:
    """무엇이 바뀌었는지 분류한다(해시만으로는 알 수 없으므로 revision 행에 기록).

    계약 §5-1: `change_kind ∈ {new, text_edit, attachment_added, attachment_removed,
    attachment_renamed, mixed}`.
    """
    if previous is None:
        return "new"
    prev_text, prev_entries = previous
    cur_text, cur_entries = current
    if prev_text == cur_text and list(prev_entries) == list(cur_entries):
        # 호출자가 같은 revision 을 다시 분류한 경우 — 새 revision 이 아니다.
        return "new"

    text_changed = prev_text != cur_text
    prev_map = digests_by_key(prev_entries)
    cur_map = digests_by_key(cur_entries)
    added = [key for key in cur_map if key not in prev_map]
    removed = [key for key in prev_map if key not in cur_map]
    # 같은 경로인데 바이트가 달라진 첨부 = 제거 + 추가로 본다(내용 교체).
    changed = [key for key in prev_map if key in cur_map and prev_map[key] != cur_map[key]]
    if changed:
        added = added + changed
        removed = removed + changed

    if not added and not removed:
        return "text_edit" if text_changed else "mixed"

    renamed_only = (
        not text_changed
        and not changed
        and bool(added)
        and bool(removed)
        and sorted(cur_map[key] for key in added) == sorted(prev_map[key] for key in removed)
    )

    kinds: set[str] = set()
    if text_changed:
        kinds.add("text_edit")
    if renamed_only:
        kinds.add("attachment_renamed")
    else:
        if added:
            kinds.add("attachment_added")
        if removed:
            kinds.add("attachment_removed")
    if len(kinds) == 1:
        return next(iter(kinds))
    return "mixed"
