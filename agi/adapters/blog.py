"""AGI-03 블로그 소스 어댑터 — 발견(목록) → 원문 보관 → revision/삭제/권한 처리.

범위 (Planner 지시 2026-10-11)
------------------------------
* **fixture / 임시 스키마 한정**. 라이브 수집·네트워크 호출·legacy 테이블 쓰기 없음.
  실제 HTTP 응답은 호출자가 주입하는 `fetch` 콜러블로만 들어온다(테스트는 fixture dict).
* 블로그 소스의 (a) 목록 발견, (b) 본문 원문 보관 + revision, (c) 삭제/권한 처리,
  (d) **검색 결과 한계 표시**를 담당한다.

설계 규칙 — 계약 AGI-01 §5-2(revision·tombstone)·§6(완전성)·§9-4(노출 경계), 수용기준 T2
--------------------------------------------------------------------------------------
R1. 삭제·비공개·접근불가는 **행 삭제가 아니라 tombstone**(사유 코드 + 관측시각)이다.
    revision 은 그대로 보존된다(계약 §5-2).
R2. **권한 오류를 삭제로 기록하지 않는다** — 401/403 은 `access_state='restricted'` 로
    구분한다(계약 §5-2 명문). 404/410 만 `deleted` 다.
R3. **목록이 불완전하면(부분 페이지·검색 결과 잘림) 삭제 스윕을 하지 않는다(fail-closed).**
    부분 목록으로 스윕하면 살아 있는 글이 대량 tombstone 된다 = 데이터 유실.
    이 경우 `coverage='denominator_unknown'` 과 `limitations` 로 한계를 표시한다.
R4. 항목별 **직접 증거**(목록이 withdrawn 표시, fetch 404/410)는 목록 완전성과 무관하게
    인정한다 — 그 글 자체에 대한 증거이지 "목록에 없다"는 추론이 아니다.
R5. 재실행은 멱등이다 — 엔티티(문서·revision) 추가 0, 실행 원장(`ingestion_runs`)만 1행.
R6. `revision_hash` 범위는 **본문 전용**(`body_only`)이다. 첨부 바이트를 보관하지 않아
    `attachment_digest = sha256(바이트 원문)` 을 만들 수 없다 → 미디어 목록은 `media_refs`
    선언 참조로만 남고 해시에 들어가지 않는다. **미디어만 바뀐 글은 새 revision 이 아니다.**
    (첨부 바이트 경로는 AGI-04 인수 항목.)
R7. 어댑터는 자기 `scope` 만 다룬다 — 블로그 어댑터가 `scope='blog'` 가 아닌 소스를
    넘겨받으면 조용히 진행하지 않고 예외로 멈춘다(어댑터 교차 오용 방지).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from agi import revision
from agi.adapters.errors import FetchPolicyError
from agi.repository import (
    ResearchRepository,
    resolve_store_original_body,
    should_create_revision_for_policy,
)
from agi.timeutil import now_utc, parse_legacy_kst

ADAPTER_VERSION = "blog/v1"

BLOG_SOURCE_KIND = "blog"
BLOG_SCOPE = "blog"

# --- tombstone 사유 코드(자유문장 금지: 집계·테스트가 가능해야 한다) -----------------
REASON_ABSENT_FROM_COMPLETE_LISTING = "absent_from_complete_listing"
REASON_LISTING_MARKED_WITHDRAWN = "listing_marked_withdrawn"
REASON_LISTING_MARKED_PRIVATE = "listing_marked_private"
REASON_FETCH_GONE = "fetch_status_gone"  # 404/410
REASON_FETCH_RESTRICTED = "fetch_status_restricted"  # 401/403
REASON_FETCH_UNAVAILABLE = "fetch_status_unavailable"  # 429/5xx — 일시적
REASON_DETAIL_MARKED_PRIVATE = "detail_marked_private"  # 2026-10-13 외부 검토 R3

# HTTP 상태 → (access_state, 사유). 목록에 없는 상태 코드는 임의 해석하지 않는다.
_STATUS_MAP: dict[int, tuple[str, str]] = {
    404: ("deleted", REASON_FETCH_GONE),
    410: ("deleted", REASON_FETCH_GONE),
    401: ("restricted", REASON_FETCH_RESTRICTED),
    403: ("restricted", REASON_FETCH_RESTRICTED),
    429: ("unavailable", REASON_FETCH_UNAVAILABLE),
    500: ("unavailable", REASON_FETCH_UNAVAILABLE),
    502: ("unavailable", REASON_FETCH_UNAVAILABLE),
    503: ("unavailable", REASON_FETCH_UNAVAILABLE),
    504: ("unavailable", REASON_FETCH_UNAVAILABLE),
}

# 원문 텍스트를 그대로 보관한 단일 출처 상태 — 승격 불가 등급(계약 §8 G4).
DEFAULT_REVISION_QUALITY = "single_source"


def classify_fetch_status(status: int) -> tuple[str, str | None]:
    """fetch HTTP 상태를 `(access_state, tombstone 사유)` 로 분류한다.

    200 은 `('ok', None)`. 매핑에 없는 상태는 추측하지 않고 `ValueError` —
    모르는 상태를 '정상'이나 '삭제'로 뭉개면 조용한 오분류가 된다.
    """
    if int(status) == 200:
        return "ok", None
    try:
        return _STATUS_MAP[int(status)]
    except KeyError:  # pragma: no cover - 방어적
        raise ValueError(f"unmapped fetch status: {status!r}") from None


# --------------------------------------------------------------------- 입력 자료구조
@dataclass(frozen=True)
class ListingEntry:
    """목록에 보인 글 1건 — 발견 단계의 사실만 담는다(본문 없음)."""

    external_id: str
    title: str | None = None
    url: str | None = None
    published_at: str | None = None  # 소스가 준 시각 텍스트(없으면 None)
    language: str | None = None
    # 목록 자체가 "삭제됨/비공개" 로 표시한 글. 본문은 없어도 항목 증거로 인정한다(R4).
    withdrawn: bool = False
    is_private: bool = False

    def __post_init__(self) -> None:
        if not str(self.external_id).strip():
            raise ValueError("ListingEntry.external_id must be non-empty")


@dataclass(frozen=True)
class ListingPage:
    """목록 1페이지 + **완전성 근거**.

    `complete` 는 이 응답이 소스의 전체 글을 덮었다는 뜻이고, `truncated` 는 검색/페이지
    한계로 잘렸다는 뜻이다. 둘 다 아니면 삭제 스윕을 하지 않는다(R3, fail-closed).
    """

    entries: tuple[ListingEntry, ...]
    complete: bool = False
    truncated: bool = False
    limitation: str | None = None
    # (2026-10-13 외부 검토 R4) 이 crawl walk 안에서 이 페이지가 몇 번째인가(1부터).
    # '마지막 페이지에 도달했다'(`complete`)와 '소스 전체의 완전한 목록을 확보했다'는
    # 별개의 사실이다 — 다중 페이지 소스의 마지막 페이지 하나만 단독으로 들어오면
    # (이전 페이지들의 글이 이 페이지의 '목격 집합'에 없다) 그 하나만으로 스윕하면
    # 이전 페이지의 살아 있는 글이 삭제로 오판된다. `page_index == 1` 인 경우에만
    # 이 페이지 자체가 전체 목록(= 한 페이지짜리 소스)일 수 있다. 여러 페이지를 모아
    # 스윕하려면 `BlogAdapter.ingest_full_listing`(전 페이지 연속성 검증)을 쓴다.
    page_index: int = 1
    # (2026-10-13 2차 외부 검토 P1) `truncated=True` 가 된 **이유**가 "소스가 다음 페이지가
    # 있다고 스스로 말했다"(hasNextPage 류 마커)인지, 아니면 "이 페이지 자체를 다 못 읽었다"
    # (max_entries 로 잘림 등)인지는 다르다. 후자는 **이 페이지 안에서도** 글이 빠졌을 수
    # 있어, `ingest_full_listing` 이 그런 페이지를 '정상적인 중간 페이지'로 받아들이면
    # 그 빠진 글이 끝까지 어떤 페이지에도 나타나지 않아 '완전한 목록'이 거짓이 된다.
    # `page_continues=True` 일 때만 "이 페이지는 (이 페이지 몫을) 전부 읽었고, 그 다음에
    # 더 있다"는 뜻이다.
    page_continues: bool = False

    def __post_init__(self) -> None:
        if self.complete and self.truncated:
            raise ValueError("ListingPage cannot be both complete and truncated")
        if self.truncated and not self.limitation:
            raise ValueError("truncated listing must carry a limitation note (검색 한계 표시)")
        if self.page_index < 1:
            raise ValueError("page_index must be >= 1")

    @property
    def tombstones_allowed(self) -> bool:
        """'목록에 없음' 추론으로 **이 페이지 하나만으로** tombstone 을 만들 수 있는가.

        R4: `page_index == 1` 일 때만 "이 페이지가 곧 전체 목록"이 성립한다(한 페이지짜리
        소스). 2 번째 이후 페이지는 `ingest_full_listing` 으로 전 페이지를 모아야 한다.
        """
        return bool(self.complete and not self.truncated and self.page_index == 1)

    @property
    def coverage(self) -> str:
        return "known" if self.tombstones_allowed else "denominator_unknown"

    @property
    def limitations(self) -> list[str]:
        notes: list[str] = []
        if self.truncated and self.limitation:
            notes.append(f"listing_truncated:{self.limitation}")
        elif self.complete and self.page_index != 1:
            notes.append(
                "listing_page_index_gt_1:마지막 페이지이지만 이전 페이지가 이 호출에 없어 "
                "단일 페이지 스윕을 보류했다 — ingest_full_listing으로 전 페이지를 모아 전달할 것"
            )
        elif not self.complete:
            notes.append("listing_partial:목록이 소스 전체를 덮지 않았다 — 삭제 판정 보류")
        return notes

    @property
    def external_ids(self) -> set[str]:
        return {str(entry.external_id) for entry in self.entries}


@dataclass(frozen=True)
class PostDetail:
    """원문 1건 — fetch 가 200 으로 돌려준 내용."""

    body_text: str | None = None
    media_refs: tuple[str, ...] = ()
    is_private: bool = False
    language: str | None = None
    title: str | None = None

    @property
    def media_list(self) -> list[str]:
        return sorted({str(ref).strip() for ref in self.media_refs if str(ref).strip()})


@dataclass(frozen=True)
class FetchResult:
    """fetch 콜러블의 반환 — HTTP 상태 + (200 일 때) 원문."""

    http_status: int = 200
    detail: PostDetail | None = None

    def __post_init__(self) -> None:
        if self.http_status == 200 and self.detail is None:
            raise ValueError("FetchResult with status 200 must carry a detail")
        if self.http_status != 200 and self.detail is not None:
            # 접근 불가 응답의 본문을 원문으로 저장하지 않는다(권한 오류를 데이터로 승격 금지).
            raise ValueError("FetchResult with non-200 status must not carry a detail")


def parse_listing(payload: dict) -> ListingPage:
    """fixture dict → `ListingPage`. 어댑터와 테스트가 같은 파서를 쓴다."""
    if not isinstance(payload, dict):
        raise TypeError("listing payload must be a dict")
    raw_entries = payload.get("entries")
    if raw_entries is None:
        raise ValueError("listing payload requires 'entries'")
    entries = tuple(
        ListingEntry(
            external_id=str(item["external_id"]),
            title=item.get("title"),
            url=item.get("url"),
            published_at=item.get("published_at"),
            language=item.get("language"),
            withdrawn=bool(item.get("withdrawn", False)),
            is_private=bool(item.get("is_private", False)),
        )
        for item in raw_entries
    )
    return ListingPage(
        entries=entries,
        complete=bool(payload.get("complete", False)),
        truncated=bool(payload.get("truncated", False)),
        limitation=payload.get("limitation"),
    )


def parse_detail(payload: dict) -> PostDetail:
    """fixture dict → `PostDetail`."""
    if not isinstance(payload, dict):
        raise TypeError("detail payload must be a dict")
    refs = payload.get("media_refs") or ()
    if isinstance(refs, str):
        refs = (refs,)
    return PostDetail(
        body_text=payload.get("body_text"),
        media_refs=tuple(str(ref) for ref in refs),
        is_private=bool(payload.get("is_private", False)),
        language=payload.get("language"),
        title=payload.get("title"),
    )


def fixture_fetch(details: dict[str, Any]) -> Callable[[str], FetchResult]:
    """fixture 기반 `fetch` 콜러블을 만든다.

    값은 `PostDetail`/`FetchResult`/dict 중 하나다. dict 는 상세 payload 로 해석하며,
    `{"http_status": 403}` 처럼 상태만 주면 접근 불가 응답이 된다.
    **네트워크를 쓰지 않는다** — 이 슬라이스의 계약(Planner: fixture/임시 스키마 한정).
    """

    def fetch(external_id: str) -> FetchResult:
        raw = details.get(str(external_id))
        if raw is None:
            return FetchResult(http_status=404)
        if isinstance(raw, FetchResult):
            return raw
        if isinstance(raw, PostDetail):
            return FetchResult(http_status=200, detail=raw)
        if isinstance(raw, dict):
            status = int(raw.get("http_status", 200))
            if status != 200:
                return FetchResult(http_status=status)
            body = {key: value for key, value in raw.items() if key != "http_status"}
            return FetchResult(http_status=200, detail=parse_detail(body))
        raise TypeError(f"unsupported fixture value for {external_id!r}: {type(raw)!r}")

    return fetch


# --------------------------------------------------------------------- 어댑터
@dataclass
class _Counters:
    listed: int = 0
    stored: int = 0
    duplicates: int = 0
    deleted: int = 0
    restricted: int = 0
    unavailable: int = 0
    errors: int = 0
    resurrected: int = 0
    tombstones_from_listing: int = 0
    tombstones_from_sweep: int = 0
    sweep_performed: bool = False
    sweep_skipped: int = 0
    # store_original='no' 로 내용 보관을 하지 않은 건수(2026-10-13 외부 검토 R1).
    not_stored_by_policy: int = 0
    policy_blocked: dict[str, int] = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)


class BlogAdapter:
    """블로그 소스 어댑터 — 저장소(`ResearchRepository`)만 통해서 DB 에 접근한다."""

    def __init__(self, *, schema: str | None = None, host: str | None = None) -> None:
        self.schema = schema
        self.host = host

    # ------------------------------------------------------------------ 소스
    def ensure_source(
        self,
        conn,
        *,
        blog_key: str,
        source_owner: str = "owner",
        display_name: str | None = None,
        requested_from=None,
        oldest_available=None,
    ) -> str:
        """블로그 소스를 등록한다(`scope='blog'`). 기본 `publish_summary='denied'` 유지."""
        repo = ResearchRepository(conn, schema=self.schema)
        return repo.ensure_source(
            source_kind=BLOG_SOURCE_KIND,
            source_owner=source_owner,
            source_native_id=blog_key,
            display_name=display_name,
            scope=BLOG_SCOPE,
            coverage="denominator_unknown",
            requested_from=requested_from,
            oldest_available=oldest_available,
        )

    # ------------------------------------------------------------------ 수집
    def ingest_page(
        self,
        conn,
        source_id: str,
        page: ListingPage,
        fetch: Callable[[str], FetchResult],
        *,
        run_kind: str = "incremental",
        duplicate_of_run_id: int | None = None,
    ) -> dict:
        """목록 1페이지를 반영한다: 원문/revision 저장 → 삭제·권한 처리 → 한계 기록.

        반환값은 실행 원장과 같은 사실을 담은 요약이며, 삭제 스윕을 **했는지/건너뛰었는지**를
        반드시 포함한다(스윕을 건너뛴 사실을 숨기면 "삭제 0건"이 거짓 근거가 된다).
        """
        repo = ResearchRepository(conn, schema=self.schema)
        source = repo.get_source(source_id)
        if source is None:
            raise KeyError(f"unknown source_id: {source_id}")
        if source["scope"] != BLOG_SCOPE:  # R7
            raise ValueError(
                f"blog adapter refuses source scope {source['scope']!r} (expected {BLOG_SCOPE!r})"
            )
        # R1(2026-10-13 외부 검토): 원문 보관 권한은 fetch·저장 전에 한 번 읽어 모든 항목에 적용한다
        # — 소스마다 한 번만 읽고(실행 중 값이 바뀌어도 이 실행은 그 시작 값으로 일관되게 처리한다).
        store_original = source["store_original"]

        counters = _Counters(listed=len(page.entries), limitations=list(page.limitations))
        run_params = json.dumps(
            {
                "adapter": ADAPTER_VERSION,
                "complete": page.complete,
                "truncated": page.truncated,
                "limitation": page.limitation,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        run_id = repo.begin_run(
            source_id=source_id,
            run_kind=run_kind,
            host=self.host,
            run_params=run_params,
            duplicate_of_run_id=duplicate_of_run_id,
        )

        try:
            for entry in page.entries:
                self._apply_entry(repo, source_id, entry, fetch, run_id, counters, store_original)
            self._sweep_deletions(repo, source_id, page, counters)
        except Exception as exc:  # 행 단위 실패를 숨기지 않는다
            repo.finish_run(
                run_id,
                status="failed",
                listed_count=counters.listed,
                stored_count=counters.stored,
                skipped_duplicate=counters.duplicates,
                skipped_deleted=counters.deleted,
                skipped_restricted=counters.restricted,
                skipped_error=counters.unavailable + counters.errors + 1,
                skipped_no_store_policy=counters.not_stored_by_policy,
                error=str(exc),
            )
            conn.commit()
            raise

        status = "success" if counters.unavailable == 0 and counters.errors == 0 else "partial"
        repo.finish_run(
            run_id,
            status=status,
            cursor_end=self._cursor_end(page),
            listed_count=counters.listed,
            stored_count=counters.stored,
            skipped_duplicate=counters.duplicates,
            skipped_deleted=counters.deleted,
            skipped_restricted=counters.restricted,
            skipped_error=counters.unavailable + counters.errors,
            skipped_no_store_policy=counters.not_stored_by_policy,
        )
        repo.increment_source_progress(
            source_id,
            listed=counters.listed,
            stored=counters.stored,
            skipped_duplicate=counters.duplicates,
            skipped_deleted=counters.deleted,
            skipped_restricted=counters.restricted,
            skipped_error=counters.unavailable + counters.errors,
            skipped_no_store_policy=counters.not_stored_by_policy,
            page_cursor=self._cursor_end(page),
            last_collected_at=now_utc(),
            coverage=page.coverage,
        )
        return {
            "source_id": source_id,
            "run_id": run_id,
            "run_kind": run_kind,
            "status": status,
            "adapter": ADAPTER_VERSION,
            "listed_count": counters.listed,
            "stored_count": counters.stored,
            "skipped_duplicate": counters.duplicates,
            "skipped_deleted": counters.deleted,
            "skipped_restricted": counters.restricted,
            "skipped_unavailable": counters.unavailable,
            "skipped_error": counters.errors,
            "skipped_no_store_policy": counters.not_stored_by_policy,
            "store_original": store_original,
            "resurrected_count": counters.resurrected,
            "tombstones_from_listing": counters.tombstones_from_listing,
            "tombstones_from_sweep": counters.tombstones_from_sweep,
            # 삭제 스윕을 했는지 여부는 '삭제 0건'의 의미를 바꾼다 — 반드시 노출한다.
            "tombstone_sweep_performed": counters.sweep_performed,
            "tombstone_sweep_skipped_count": counters.sweep_skipped,
            "coverage": page.coverage,
            "policy_blocked_counts": dict(sorted(counters.policy_blocked.items())),
            "limitations": counters.limitations,
            "exhausted": True,
        }

    def ingest_known_posts(
        self,
        conn,
        source_id: str,
        entries,
        fetch: Callable[[str], FetchResult],
        *,
        run_kind: str = "verify",
        duplicate_of_run_id: int | None = None,
    ) -> dict:
        """**특정 글만** 수집한다 — 목록(PostList 류) 탐색을 하지 않는다(2026-10-13 사용자 지시).

        `run_kind` 기본값은 `'verify'`다(`ingestion_runs.run_kind` CHECK 제약의 4값 중 하나 —
        새 값을 추가하려면 migration 이 필요하므로, '사전에 알고 있는 특정 글을 다시 확인한다'
        는 의미에 가장 가까운 기존 값을 쓴다).

        전체 목록을 긁는 대신, 호출자가 이미 알고 있는 글(`ListingEntry` 목록, 보통
        `naver_blog_listing.detail_url()` 로 만든 상세 URL)만 가져온다. 네이버 상세 페이지
        (`/{blog_id}/{log_no}`)는 robots **허용** 경로다 — 이 메서드는 `PostList.naver`
        (robots 차단)를 전혀 거치지 않으므로 `CollectionApproval` 승인이 필요 없다.

        완전성 주장을 **절대** 하지 않는다 — 호출자가 무엇을 넘기든 내부적으로
        `complete=False, truncated=False`(=coverage 'denominator_unknown') 로 강제한다.
        손으로 고른 일부 글 목록을 '전체 목록'으로 오인해 스윕(없는 글 = 삭제)하면 안
        되기 때문이다(R3/R4 원칙과 동일한 fail-closed).
        """
        page = ListingPage(entries=tuple(entries), complete=False, truncated=False, page_index=1)
        return self.ingest_page(
            conn, source_id, page, fetch, run_kind=run_kind, duplicate_of_run_id=duplicate_of_run_id
        )

    def ingest_full_listing(
        self,
        conn,
        source_id: str,
        pages,
        fetch: Callable[[str], FetchResult],
        *,
        run_kind: str = "incremental",
        duplicate_of_run_id: int | None = None,
    ) -> dict:
        """여러 목록 페이지를 **한 run**으로 합쳐 처리한다 — 완전한 목록 확보를 전 페이지
        연속성으로 증명한다(2026-10-13 외부 검토 R4).

        `ingest_page` 에 페이지를 하나씩 따로 넘기면, 그 호출은 **이 페이지에 없는 글**만
        알 수 있다 — 다른 호출에서 저장한 이전 페이지의 글은 이 호출의 '목격 집합'에
        없으므로, 마지막 페이지 하나만으로 스윕하면 이전 페이지의 살아 있는 글이 삭제로
        오판된다. 이 메서드는 그래서 전 페이지를 모은 **뒤에** 한 번만 스윕을 판단한다.

        스윕은 다음을 **전부** 만족할 때만 수행한다(하나라도 어긋나면 합친 목록을 부분
        목록으로 취급해 건너뛴다 — fail-closed, 거짓으로 '완전하다'고 선언하지 않는다):
          * `pages` 의 `page_index` 가 1부터 빠짐없이 연속한다(중복·누락 없음).
          * 마지막 페이지를 제외한 모든 페이지가 `truncated=True and page_continues=True`
            (다음 페이지가 있다고 **스스로** 말했다 — `hasNextPage`/`nextPage` 류 마커).
            `page_continues=False` 인 채 잘린 페이지(예: 항목 수 상한으로 잘림)는 **그
            페이지 자신의 내용조차 다 못 읽었다는 뜻**이라 중간 페이지로 받아들이지 않는다
            (2026-10-13 2차 외부 검토 P1 — 이전엔 `truncated` 불만 보고 승인했다).
          * 마지막 페이지가 `complete=True and truncated=False`(스스로 끝이라고 말했다).
        """
        if not pages:
            raise ValueError("ingest_full_listing requires at least one page")
        ordered = sorted(pages, key=lambda p: p.page_index)
        contiguous = [p.page_index for p in ordered] == list(range(1, len(ordered) + 1))
        non_final_said_more = all(
            p.truncated and not p.complete and p.page_continues for p in ordered[:-1]
        )
        final_said_last = ordered[-1].complete and not ordered[-1].truncated
        full_listing_confirmed = contiguous and non_final_said_more and final_said_last

        extra_notes: list[str] = []
        if not full_listing_confirmed:
            if not contiguous:
                extra_notes.append(
                    f"listing_pages_not_contiguous:{[p.page_index for p in ordered]}"
                )
            if not non_final_said_more:
                bad = [
                    p.page_index
                    for p in ordered[:-1]
                    if not (p.truncated and not p.complete and p.page_continues)
                ]
                extra_notes.append(f"listing_middle_page_not_marked_truncated:{bad}")
            if not final_said_last:
                extra_notes.append("listing_last_page_not_marked_complete")

        seen: set[str] = set()
        merged_entries: list[ListingEntry] = []
        for pg in ordered:
            for entry in pg.entries:
                key = str(entry.external_id)
                if key in seen:
                    continue
                seen.add(key)
                merged_entries.append(entry)

        merged = ListingPage(
            entries=tuple(merged_entries),
            complete=full_listing_confirmed,
            truncated=not full_listing_confirmed,
            limitation=None if full_listing_confirmed else "merged_pages_incomplete",
            page_index=1,
        )
        result = self.ingest_page(
            conn, source_id, merged, fetch, run_kind=run_kind, duplicate_of_run_id=duplicate_of_run_id
        )
        if extra_notes:
            result["limitations"] = list(result.get("limitations", [])) + extra_notes
        return result

    # ------------------------------------------------------------------ 내부
    @staticmethod
    def _cursor_end(page: ListingPage) -> str | None:
        if not page.entries:
            return None
        return max(str(entry.external_id) for entry in page.entries)

    def _apply_entry(
        self,
        repo: ResearchRepository,
        source_id: str,
        entry: ListingEntry,
        fetch: Callable[[str], FetchResult],
        run_id: int,
        counters: _Counters,
        store_original: str,
    ) -> None:
        external_id = str(entry.external_id)

        published_at = parse_legacy_kst(entry.published_at)
        published_at_source = "source_meta"
        first_seen_at = now_utc()
        if published_at is None:
            # 계약 §4: 발표시각 미확보 → 수집시각을 하한으로 대체하고 그 사실을 표기한다.
            published_at = first_seen_at
            published_at_source = "collected_at_lower_bound"

        # (1) 항목별 직접 증거 — 목록 자체가 "삭제됨" 이라고 말한 글. fetch 하지 않는다.
        if entry.withdrawn:
            document_id = self._store_placeholder(repo, source_id, entry, run_id, published_at, published_at_source, first_seen_at)
            self._mark(repo, document_id, "deleted", REASON_LISTING_MARKED_WITHDRAWN)
            counters.deleted += 1
            counters.tombstones_from_listing += 1
            return

        # (2) 비공개 표시 — **본문을 가져오지 않는다**. 비공개 글의 본문을 원문 보관 테이블로
        #     끌어오면 노출 면적만 넓어진다(가져올 근거가 없으므로 fail-closed).
        if entry.is_private:
            document_id = self._store_placeholder(
                repo, source_id, entry, run_id, published_at, published_at_source, first_seen_at, is_private=True
            )
            self._mark(repo, document_id, "restricted", REASON_LISTING_MARKED_PRIVATE)
            counters.restricted += 1
            return

        try:
            result = fetch(external_id)
        except FetchPolicyError as exc:
            # **가져오지 않기로 한 결정**(스킴·호스트목록·주소등급·리다이렉트·크기·타입·타임아웃).
            # 서버가 준 상태가 아니므로 `restricted`/`deleted` 로 승격하지 않는다 — 정책 차단과
            # 권한 오류를 같은 값으로 뭉개면 원인 추적이 불가능해진다. 본문이 없으니 revision 도 없다.
            # 실행 전체를 실패시키지 않는 이유: 차단은 URL 단위 결정이고, 한 건의 오설정이
            # 나머지 글의 수집을 막을 이유가 없다. 대신 사유 코드를 문서·실행 요약에 남긴다.
            document_id = self._store_placeholder(
                repo, source_id, entry, run_id, published_at, published_at_source, first_seen_at
            )
            self._mark(repo, document_id, "unavailable", exc.reason())
            counters.unavailable += 1
            counters.policy_blocked[exc.code] = counters.policy_blocked.get(exc.code, 0) + 1
            note = f"policy_blocked:{exc.code}"
            if note not in counters.limitations:
                counters.limitations.append(note)
            return

        access_state, reason = classify_fetch_status(result.http_status)
        detail = result.detail
        # (2026-10-13 외부 검토 R3) 목록은 이 글을 공개로 보여줬어도(또는 예전에는 실제로
        # 공개였어도), **상세 응답 자체**가 비공개라고 말하면 그 사실을 신뢰한다 — 목록의
        # 과거 진술이나 이전 수집 결과보다 지금의 직접 증거가 우선한다. 이 경우 fetch 가
        # 200 이어도 본문을 원문 보관 테이블에 넣지 않는다(비공개 글의 본문을 가져오지
        # 않는다는 R1 비공개 규칙과 같은 원칙 — 다만 그 사실을 "fetch 전" 이 아니라
        # "fetch 응답을 읽은 후" 에만 알 수 있었을 뿐이다).
        if access_state == "ok" and detail is not None and detail.is_private:
            access_state, reason = "restricted", REASON_DETAIL_MARKED_PRIVATE

        media_refs = "\n".join(detail.media_list) if (detail is not None and access_state == "ok") else ""

        document_id, _ = repo.store_document(
            source_id=source_id,
            external_id=external_id,
            canonical_uri=entry.url,
            title=(detail.title if detail and detail.title else entry.title),
            language=(detail.language if detail and detail.language else entry.language),
            published_at=published_at,
            published_at_source=published_at_source,
            first_seen_at=first_seen_at,
            is_private=bool(detail.is_private) if detail is not None else False,
            media_refs=media_refs,
            first_run_id=run_id,
        )

        if access_state != "ok":
            # R2/R3: 권한 오류(상세의 비공개 포함)를 삭제로 기록하지 않는다. 본문 없이는
            # revision 도 만들지 않는다.
            self._mark(repo, document_id, access_state, reason)
            if access_state == "deleted":
                counters.deleted += 1
            elif access_state == "restricted":
                counters.restricted += 1
            else:
                counters.unavailable += 1
            return

        if not should_create_revision_for_policy(store_original):
            # R1: store_original='no' — 변경 이력·본문 모두 남기지 않는다(문서 존재 사실만).
            counters.not_stored_by_policy += 1
        else:
            created = self._store_revision(repo, document_id, entry, detail, run_id, store_original)
            if created:
                counters.stored += 1
            else:
                counters.duplicates += 1
        # 살아 있는 글: 이전에 tombstone 이었다면 되돌린다(R1 — 기록은 지우지 않는다).
        if self._mark(repo, document_id, "ok", None):
            counters.resurrected += 1

    def _store_placeholder(
        self,
        repo: ResearchRepository,
        source_id: str,
        entry: ListingEntry,
        run_id: int,
        published_at,
        published_at_source: str,
        first_seen_at,
        *,
        is_private: bool = False,
    ) -> str:
        """본문을 가져오지 않는 항목(삭제·비공개·정책 차단)의 **문서 행만** 만든다.

        본문이 없으므로 revision 을 만들지 않는다 — "본문 없는 revision" 은 "빈 글" 과
        구분되지 않아 나중에 데이터로 오독된다.
        """
        document_id, _ = repo.store_document(
            source_id=source_id,
            external_id=str(entry.external_id),
            canonical_uri=entry.url,
            title=entry.title,
            language=entry.language,
            published_at=published_at,
            published_at_source=published_at_source,
            first_seen_at=first_seen_at,
            is_private=is_private,
            first_run_id=run_id,
        )
        return document_id

    def _mark(self, repo: ResearchRepository, document_id: str, access_state: str, reason: str | None) -> bool:
        """상태를 전이하고 **실제로 바뀐 경우만** True 를 돌려준다.

        바뀌지 않은 전이에 True 를 돌려주면 '되돌림(resurrected) 건수'가 매 실행마다 부풀어
        실행 요약이 사실과 달라진다.
        """
        before = repo.get_document(document_id, visibility="internal")
        if before is not None and before.get("access_state") == access_state:
            return False
        repo.mark_access_state(document_id, access_state=access_state, reason=reason)
        return True

    @staticmethod
    def _previous_state(repo: ResearchRepository, document_id: str):
        previous = repo.current_revision(document_id)
        if previous is None:
            return None
        entries = [entry for entry in str(previous["attachments_canonical"] or "").split("\n") if entry]
        return (previous["text_digest"], entries)

    def _store_revision(self, repo, document_id, entry, detail, run_id, store_original: str) -> bool:
        """revision 1건 저장. `store_original` 이 'yes' 가 아니면 **본문은 메모리에서만**
        해시·변경 감지에 쓰고, 저장 계층에는 넘기지 않는다(R1, `resolve_store_original_body`).
        호출 시점에 `should_create_revision_for_policy`로 'no' 를 걸러냈어야 한다.
        """
        body = (detail.body_text or "") if detail is not None else ""
        revision_hash, text_digest, entries = revision.compute_revision_hash(body)
        change_kind = revision.classify_change(self._previous_state(repo, document_id), (text_digest, entries))
        stored_body = resolve_store_original_body(store_original, body)
        _, created = repo.store_revision(
            document_id=document_id,
            revision_hash=revision_hash,
            text_digest=text_digest,
            change_kind=change_kind,
            collected_at=now_utc(),
            body_text=stored_body,
            body_uri=(entry.url if stored_body is not None else None),
            attachments_canonical=revision.canonical_attachment_string(entries),
            attachment_count=len(entries),
            quality=DEFAULT_REVISION_QUALITY,
            pit_eligible=parse_legacy_kst(entry.published_at) is not None,
            run_id=run_id,
        )
        return created

    def _sweep_deletions(
        self, repo: ResearchRepository, source_id: str, page: ListingPage, counters: _Counters
    ) -> None:
        """목록에 없는 글을 tombstone 한다 — **완전한 목록일 때만**(R3, fail-closed)."""
        if not page.tombstones_allowed:
            counters.sweep_performed = False
            counters.limitations.append(
                "tombstone_sweep_skipped:목록이 불완전해 '목록에 없음'을 삭제로 해석하지 않았다"
            )
            return
        counters.sweep_performed = True
        seen = page.external_ids
        for ref in repo.list_document_refs(source_id):
            if str(ref["external_id"]) in seen:
                continue
            if ref["access_state"] != "ok":
                counters.sweep_skipped += 1
                continue
            repo.mark_access_state(
                ref["document_id"],
                access_state="deleted",
                reason=REASON_ABSENT_FROM_COMPLETE_LISTING,
            )
            counters.deleted += 1
            counters.tombstones_from_sweep += 1
