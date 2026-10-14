"""네이버 블로그 목록 HTML → `ListingPage` **오프라인 변환기** (AGI-03 제안 1).

.. warning::

   **이 모듈은 수집 허가가 아니다.** `blog.naver.com/PostList.naver` 는 2026-10-11 실측
   기준 `User-agent: *` 그룹에서 `Disallow` 다(아래 `ROBOTS_EVIDENCE`). 따라서 이 변환기가
   만들어 내는 `ListingPage` 는 **정책 승인 전에는 어떤 실제 요청의 근거도 될 수 없다**.

   이 사실은 문서 주석만이 아니라 **코드와 테스트로 강제**한다:

   * 네트워크를 부르는 코드가 이 모듈에 **없다**(stdlib `html.parser` 만 쓴다).
     `tests/test_agi_naver_blog_listing.py::NoNetworkImportsTests` 가 모듈 소스를 검사해
     `requests`/`urllib`/`http.client`/`socket` 수입이 생기면 실패한다.
   * 목록 URL 을 만드는 `build_listing_url()` 은 **명시 승인 객체 없이는 항상 예외**다
     (fail-closed). 승인 객체는 `CollectionApproval(allow_postlist_path=True)` 뿐이다.
   * 로봇 판정은 우리 정책 계층(`agi.adapters.collection_policy`)으로 **다시 계산**해
     테스트에 고정한다(실측 캡처 robots 규칙 + `/PostList.naver` → 차단).

배경 (읽기 전용 실측, `docs/AGI_03_BLOG_SOURCE_INVENTORY_20261011.md` §3)

* 현행 프로덕션 `Sector_define/blog_parser.py` 는 이 차단 경로를 매일 07:00 호출한다.
* 상세(본문) 페이지(`/{blog_id}/{log_no}`, `/PostView.naver`)는 robots **허용**이다.
* 그래서 이 모듈이 만드는 항목 `url` 은 **상세 URL** 이고, 목록 URL 은 저장 대상이 아니다.

픽스처 출처 고지(매우 중요)

`FIXTURE_PROVENANCE = "synthetic"` — 이 슬라이스의 회귀 테스트는 **합성 픽스처**를 쓴다.
로봇이 막은 경로를 캡처할 수 없기 때문이다(캡처 자체가 정책 위반). 따라서 이 파서는
**아직 실응답으로 검증된 파서가 아니다**. B3(정책 결정) 승인 후 캡처한 표본으로 픽스처를
교체하고 `FIXTURE_PROVENANCE` 를 바꾸기 전까지, 발견 단계의 실사용을 주장하지 않는다.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser

from agi.adapters.blog import ListingEntry, ListingPage

NAVER_BLOG_LISTING_VERSION = "naver-blog-listing/v1"

NAVER_HOST = "blog.naver.com"
NAVER_POST_LIST_PATH = "/PostList.naver"

# 로봇 상태는 상수가 아니라 **실측 근거**와 함께 둔다(주장과 증거를 분리하지 않는다).
ROBOTS_STATUS = "disallowed"
ROBOTS_USER_AGENT_GROUP = "*"
ROBOTS_EVIDENCE = (
    "blog.naver.com/robots.txt → 'User-agent: *' 그룹에 'Disallow: /PostList.naver' (실측 2026-10-11)"
)
#: 이 변환기 출력이 수집 승인으로 오독되지 않도록 응답/로그에 함께 남기는 표기.
POLICY_NOTE = "postlist_path_robots_disallowed:목록 경로는 정책 차단 — 이 출력은 수집 승인 근거가 아니다"

#: 픽스처 출처. "captured" 로 바꾸려면 B3 승인 후 실표본이 있어야 한다.
FIXTURE_PROVENANCE = "synthetic"

# --- 파싱 실패/거부 사유 코드(자유문장 금지 — 집계·테스트 가능해야 한다) ---------------
REASON_EMPTY_HTML = "empty_html"
REASON_NO_ENTRIES = "no_entries_found"
REASON_NOT_TEXT = "not_text_input"

# --- 목록 완전성 한계 표기 ----------------------------------------------------------
LIMITATION_MORE_PAGES = "more_pages_available"
LIMITATION_ENTRY_LIMIT = "entry_limit_reached"
LIMITATION_NO_TERMINAL_MARKER = "no_terminal_marker"

AUTHORIZATION_REQUIRED_CODE = "naver_postlist_not_authorized"

# 10자리 이상 숫자만 글 번호로 인정한다(네이버 logNo 는 12자리대가 보통이지만 하한만 고정).
_LOG_NO_IN_HREF = re.compile(r"[?&]logNo=(\d{10,})")

# 목록의 **끝**을 스스로 말한 경우에만 완전성을 주장한다(fail-closed).
_LAST_PAGE_MARKERS = (
    re.compile(r"[\"']?isLastPage[\"']?\s*[:=]\s*true", re.IGNORECASE),
    re.compile(r"data-last-page\s*=\s*[\"']?true", re.IGNORECASE),
    re.compile(r"[\"']?lastPage[\"']?\s*[:=]\s*true", re.IGNORECASE),
)
_MORE_PAGES_MARKERS = (
    re.compile(r"[\"']?hasNextPage[\"']?\s*[:=]\s*true", re.IGNORECASE),
    re.compile(r"data-has-next\s*=\s*[\"']?true", re.IGNORECASE),
    re.compile(r"[\"']?nextPage[\"']?\s*[:=]\s*true", re.IGNORECASE),
)
# 목록이 "글이 하나도 없다"를 스스로 말한 경우.
_EMPTY_LISTING_MARKERS = (
    re.compile(r"등록된\s*글이\s*없습니다"),
    re.compile(r"포스트가\s*없습니다"),
    re.compile(r"class\s*=\s*[\"'][^\"']*\bno-post\b", re.IGNORECASE),
)

# 목록이 스스로 표시한 항목 상태(증거 없는 추론은 금지 — 표시가 없으면 False 유지).
_WITHDRAWN_ATTR_VALUES = {"withdrawn", "deleted", "true"}
_PRIVATE_ATTR_VALUES = {"private", "secret", "true"}
_WITHDRAWN_TEXTS = {"삭제된 글", "비공개로 전환된 글"}
_PRIVATE_TEXTS = {"비공개", "비공개글", "🔒"}


class PostListParseError(ValueError):
    """목록 HTML 을 해석할 수 없다 — **'0건'으로 뭉개지 않는다**(조용한 0건 금지)."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


class PostListNotAuthorized(RuntimeError):
    """목록 경로가 로봇 차단인데 승인 없이 URL 을 만들려 했다(기본 거부)."""

    def __init__(self, message: str):
        super().__init__(f"{AUTHORIZATION_REQUIRED_CODE}: {message}")


@dataclass(frozen=True)
class CollectionApproval:
    """목록 경로 사용에 대한 **명시 승인**. 기본값은 사용 불가(`allow_postlist_path=False`).

    `approval_ref` 는 결정의 근거(예: B3 공식 허용 경로 전환, 명시 예외 승인 문서)를 가리킨다.
    승인 객체를 코드가 스스로 만들지 않도록, 값은 호출부(사람/계약)가 넘긴다.
    """

    approved_by: str
    approval_ref: str
    allow_postlist_path: bool = False

    def __post_init__(self) -> None:
        if not str(self.approved_by).strip() or not str(self.approval_ref).strip():
            raise ValueError("approval requires approved_by and approval_ref")


def build_listing_url(
    blog_id: str,
    *,
    category_no: int | str | None = None,
    page: int | None = None,
    approval: CollectionApproval | None = None,
) -> str:
    """목록 URL 을 만든다 — **승인 없이는 항상 예외**(fail-closed).

    이 함수가 존재하는 이유: 네트워크를 쓰는 코드는 결국 URL 이 필요하다. URL 생성 자체를
    승인 지점으로 만들면, 나중에 누가 이 모듈을 수집 경로로 배선할 때 **승인 없이 조용히
    동작하지 않는다**.
    """
    if approval is None or not approval.allow_postlist_path:
        raise PostListNotAuthorized(
            "목록 경로는 robots 차단이다(Disallow: "
            f"{NAVER_POST_LIST_PATH}). CollectionApproval(allow_postlist_path=True) 없이는 "
            "URL 을 만들 수 없다."
        )
    key = str(blog_id or "").strip()
    if not key or any(ch in key for ch in "/?#&="):
        raise ValueError("blog_id must be a plain identifier")
    url = f"https://{NAVER_HOST}{NAVER_POST_LIST_PATH}?blogId={key}"
    if category_no is not None:
        cat = str(category_no).strip()
        if not cat.isdigit():
            raise ValueError("category_no must be numeric")
        url += f"&categoryNo={cat}"
    if page is not None:
        if not isinstance(page, int) or page < 1:
            raise ValueError("page must be a positive int")
        url += f"&currentPage={page}"
    return url


def detail_url(blog_id: str, log_no: str) -> str:
    """항목의 정본 URL = **상세 페이지**(로봇 허용 경로). 목록 URL 은 절대 저장하지 않는다."""
    key = str(blog_id or "").strip()
    if not key or any(ch in key for ch in "/?#&="):
        raise ValueError("blog_id must be a plain identifier")
    no = str(log_no or "").strip()
    if not no.isdigit():
        raise ValueError("log_no must be numeric")
    return f"https://{NAVER_HOST}/{key}/{no}"


# --------------------------------------------------------------------- HTML 파싱
class _PostListExtractor(HTMLParser):
    """`<a href="…logNo=…">제목</a>` 만 항목으로 인정하는 최소 추출기.

    실제 응답 캡처가 없는 상태(합성 픽스처)이므로, 추출 규칙은 **명시적으로 좁게** 둔다 —
    넓게 잡으면 구조가 바뀐 응답을 '많은 글'로 오독할 수 있다.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._anchors: list[dict] = []
        self.entries: list[dict] = []

    # -- HTMLParser 훅 -------------------------------------------------------
    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = {key.lower(): (value if value is not None else "") for key, value in attrs}
        if tag == "a":
            self._anchors.append({"attrs": attributes, "text": []})
            return
        if not self._anchors:
            return
        # 앵커 내부의 <time datetime="…"> 는 발행시각 증거로 인정한다.
        if tag == "time":
            stamp = attributes.get("datetime")
            if stamp:
                self._anchors[-1]["attrs"].setdefault("data-date", stamp)

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._anchors:
            self._anchors[-1]["text"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._anchors:
            return
        anchor = self._anchors.pop()
        self.entries.append(self._build_entry(anchor))

    # -- 항목 구성 -----------------------------------------------------------
    @staticmethod
    def _build_entry(anchor: dict) -> dict:
        attributes = anchor["attrs"]
        href = html.unescape(attributes.get("href") or "")
        match = _LOG_NO_IN_HREF.search(href)
        log_no = match.group(1) if match else None
        if not log_no:
            raw = (attributes.get("data-log-no") or "").strip()
            log_no = raw if raw.isdigit() and len(raw) >= 10 else None
        title = " ".join("".join(anchor["text"]).split()) or None
        return {
            "log_no": log_no,
            "title": title,
            "date": attributes.get("data-date") or attributes.get("data-published"),
            "access": (attributes.get("data-access") or "").strip().lower(),
            "withdrawn": (attributes.get("data-withdrawn") or "").strip().lower(),
            "href": href,
        }


def _has_marker(html_text: str, markers) -> bool:
    return any(pattern.search(html_text) for pattern in markers)


def normalize_published_at(raw) -> str | None:
    """목록이 준 발행시각 텍스트 → `YYYY-MM-DD[ HH:MM:SS]`. 해석 불가면 **None**.

    계약 AGI-01 §4-5: 근거 없는 값을 만들지 않는다. None 이면 어댑터가
    `collected_at_lower_bound` 로 표기하고 대체한다(추정값을 만들지 않는다).
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    text = text.replace("년", "-").replace("월", "-").replace("일", " ")
    text = re.sub(r"[./]", "-", text)
    # ISO 'T' 구분자와 '2026- 4- 26' 처럼 구분자 주변에 붙은 공백을 정규화한다.
    text = re.sub(r"(?<=\d)[Tt](?=\d)", " ", text)
    text = re.sub(r"\s*-\s*", "-", text)
    text = re.sub(r"\s+", " ", text).strip(" -")
    match = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?", text)
    if not match:
        return None
    year, month, day, hour, minute, second = match.groups()
    try:
        stamp = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    except ValueError:  # pragma: no cover - 정규식이 숫자만 잡는다
        return None
    if hour is None:
        return stamp
    return f"{stamp} {int(hour):02d}:{int(minute):02d}:{int(second or 0):02d}"


def classify_entry(entry: dict) -> tuple[bool, bool]:
    """목록이 스스로 표시한 `(withdrawn, is_private)`.

    표시가 없으면 `(False, False)` — 권한/삭제는 **목록의 진술**이거나 fetch 응답의 사실이어야
    하고, 추론으로 만들지 않는다(계약 §5-2: 권한 오류를 삭제로 기록 금지).
    """
    access = entry.get("access") or ""
    withdrawn_flag = entry.get("withdrawn") or ""
    title = entry.get("title") or ""
    is_private = bool(access) and access in _PRIVATE_ATTR_VALUES
    withdrawn = bool(withdrawn_flag) and withdrawn_flag in _WITHDRAWN_ATTR_VALUES
    if title in _PRIVATE_TEXTS:
        is_private = True
    if title in _WITHDRAWN_TEXTS:
        withdrawn = True
    return withdrawn, is_private


def parse_post_list_html(
    html_text: str,
    *,
    blog_id: str,
    max_entries: int | None = None,
    allow_empty: bool = False,
    page_index: int = 1,
) -> ListingPage:
    """목록 HTML → `ListingPage`. **네트워크·DB 접촉 0**(순수 변환).

    Args:
        html_text: 목록 페이지 HTML. `bytes` 는 받지 않는다 — 문자셋 추정은 정책 결정이며
            디코딩은 fetch 계층(`blog_fetch`)의 책임이다.
        blog_id: 항목 상세 URL 을 만들 때 쓰는 식별자.
        max_entries: 앞에서 이 개수까지만 항목으로 인정한다. 잘라냈는데 목록이 완전하다고
            주장하면 예외다(완전성 거짓 주장 금지).
        allow_empty: 항목 0건 HTML 을 '빈 목록'으로 인정할지. 기본 `False`(= 예외) —
            **0건과 파싱 실패를 같은 값으로 뭉개지 않는다**.
        page_index: 이 HTML 이 crawl walk 에서 몇 번째 페이지인지(1부터) — **호출자가
            안다**(요청한 페이지 번호). HTML 자체의 `isLastPage` 마커(=이 페이지가 끝)와는
            별개의 사실이다(R4, 2026-10-13 외부 검토): 1번째가 아닌 페이지 하나만 단독으로
            `BlogAdapter.ingest_page` 에 전달하면 이전 페이지의 글이 '목격 집합'에 없어
            스윕 시 오판된다 — 여러 페이지를 모으려면 `BlogAdapter.ingest_full_listing` 을 쓴다.

    Returns:
        `ListingPage`. 완전성은 **목록이 스스로 말한 경우에만** 참이 된다:
          * `isLastPage/lastPage=true` → `complete=True`(삭제 스윕 허용)
          * `hasNextPage/nextPage=true` → `truncated=True` + `LIMITATION_MORE_PAGES`
          * 둘 다 없음 → 부분 목록(`complete=False, truncated=False`) —
            `BlogAdapter._sweep_deletions` 가 스윕을 건너뛰고 `coverage='denominator_unknown'` 이 된다
    """
    if isinstance(html_text, (bytes, bytearray)):
        raise TypeError(f"{REASON_NOT_TEXT}: decode before parsing (fetch 계층 책임)")
    if not isinstance(html_text, str):
        raise TypeError(f"{REASON_NOT_TEXT}: html must be str, got {type(html_text)!r}")
    if not html_text.strip():
        raise PostListParseError(REASON_EMPTY_HTML, "빈 HTML 은 '0건'이 아니라 파싱 실패다")

    extractor = _PostListExtractor()
    extractor.feed(html_text)
    extractor.close()

    seen: set[str] = set()
    entries: list[ListingEntry] = []
    dropped = 0
    for raw in extractor.entries:
        log_no = raw.get("log_no")
        if not log_no or log_no in seen:
            continue
        if max_entries is not None and len(entries) >= int(max_entries):
            dropped += 1
            continue
        seen.add(log_no)
        withdrawn, is_private = classify_entry(raw)
        entries.append(
            ListingEntry(
                external_id=log_no,
                title=raw.get("title"),
                url=detail_url(blog_id, log_no),
                published_at=normalize_published_at(raw.get("date")),
                withdrawn=withdrawn,
                is_private=is_private,
            )
        )

    empty_declared = _has_marker(html_text, _EMPTY_LISTING_MARKERS)
    if not entries and not empty_declared and not allow_empty:
        raise PostListParseError(
            REASON_NO_ENTRIES,
            "logNo 항목을 찾지 못했다 — '글이 없다'와 구조 변경을 구분할 수 없으므로 0건으로 반환하지 않는다",
        )

    last_page = _has_marker(html_text, _LAST_PAGE_MARKERS)
    more_pages = _has_marker(html_text, _MORE_PAGES_MARKERS)

    complete = False
    truncated = False
    limitation: str | None = None
    page_continues = False
    if dropped:
        # 잘라냈으면 완전성을 주장할 수 없다(거짓 완전성 = 거짓 삭제 스윕). `page_continues`
        # 는 False 로 둔다 — 이 페이지 자신의 항목이 이미 잘렸으므로(2026-10-13 2차 외부
        # 검토 P1), hasNextPage 마커 여부와 무관하게 '정상적으로 끝까지 읽은 중간 페이지'가
        # 아니다. ingest_full_listing 이 이런 페이지를 중간 페이지로 받아들이면 안 된다.
        truncated = True
        limitation = LIMITATION_ENTRY_LIMIT
    elif last_page and not more_pages:
        complete = True
    elif more_pages:
        truncated = True
        limitation = LIMITATION_MORE_PAGES
        page_continues = True  # 이 페이지는 끝까지 읽었고, 소스가 다음 페이지가 있다고 말했다.
    else:
        limitation = LIMITATION_NO_TERMINAL_MARKER

    return ListingPage(
        entries=tuple(entries),
        complete=complete,
        truncated=truncated,
        limitation=limitation,
        page_index=page_index,
        page_continues=page_continues,
    )
