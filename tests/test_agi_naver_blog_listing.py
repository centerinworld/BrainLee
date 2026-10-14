"""AGI-03 제안 1 — 네이버 블로그 목록 HTML 변환기 회귀 테스트 (합성 픽스처 전용).

계약 정본: `docs/AGI_01_SOURCE_TIME_AUTH_CONTRACT_20261011.md` §5-2(revision·tombstone),
§6(완전성), §9-4(노출 경계) · 인벤토리: `docs/AGI_03_BLOG_SOURCE_INVENTORY_20261011.md` §3.

실행:
    ./venv/bin/python -m pytest tests/test_agi_naver_blog_listing.py -v

범위(Planner 지시): **네트워크 0 · DB/DDL 0 · 소스 등록 0 · 배포선 0**.
이 파일은 PostgreSQL 을 쓰지 않는다(임시 스키마조차 만들지 않는다).

픽스처 고지: 아래 HTML 은 **합성**이다. `PostList.naver` 가 로봇 차단이라 실응답을 캡처할 수
없기 때문이다(캡처 자체가 정책 위반). 따라서 이 테스트는 "파서가 우리가 정의한 표시 규칙을
지킨다"만 증명하며, **실응답 호환성은 B3 승인 후 캡처 표본으로 재검증해야 한다**.
그 미검증 사실 자체를 `FixtureProvenanceTests` 가 고정한다.
"""

from __future__ import annotations

import inspect
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from agi.adapters import naver_blog_listing as nbl
from agi.adapters.blog import ListingPage
from agi.adapters.blog_fetch import FetchPolicy
from agi.adapters.collection_policy import parse_robots

BLOG_ID = "going_tothe_moon"


# --------------------------------------------------------------------------- 픽스처
#: 로봇 규칙 캡처(2026-10-11, `https://blog.naver.com/robots.txt` 의 `User-agent: *` 규칙 발췌).
#: robots.txt 자체는 "읽으라고 공개한 정책 문서"라 캡처 보관이 문제되지 않는다 —
#: 문제가 되는 것은 **막힌 경로의 문서 본문**이며 이 테스트는 그걸 받지 않는다.
ROBOTS_TXT_CAPTURED = """\
User-agent: GPTBot
Disallow: /

User-agent: *
Disallow: /PostList.nhn
Disallow: /PostList.naver
Disallow: /post/
Disallow: /npost/
Disallow: /main/
Disallow: /buddy/
Disallow: /export/
"""

#: 완전한 목록(마지막 페이지를 스스로 선언) — 합성.
POSTLIST_LAST_PAGE_HTML = """\
<html><head><title>돈의 흐름 : 네이버 블로그</title></head><body>
<script>var pageConfig = {"categoryNo":7,"isLastPage": true,"countPerPage":3};</script>
<div class="post-list">
  <a href="/PostList.naver?blogId=going_tothe_moon&amp;logNo=224265571184&amp;categoryNo=7"
     data-date="2026. 4. 26.">AI 인프라 기업의 독주 흐름</a>
  <a href="/PostList.naver?blogId=going_tothe_moon&amp;logNo=224265014460&amp;categoryNo=7"
     data-date="2026.04.25">다음 투자기회는 <span>ESS</span>가 될 것입니다</a>
  <a href="/PostList.naver?blogId=going_tothe_moon&amp;logNo=224264230264&amp;categoryNo=7">글로벌 반도체 대표기업 한장요약</a>
</div>
</body></html>
"""

#: 다음 페이지가 있음 — 합성.
POSTLIST_MORE_PAGES_HTML = """\
<html><body>
<script>var pageConfig = {"categoryNo":7,"hasNextPage": true};</script>
<div class="post-list">
  <a href="/PostList.naver?blogId=going_tothe_moon&amp;logNo=224265571184">AI 인프라 기업의 독주 흐름</a>
  <a href="/PostList.naver?blogId=going_tothe_moon&amp;logNo=224265014460">ESS 투자기회</a>
</div>
</body></html>
"""

#: 완전성 표시가 전혀 없음 — 합성(부분 목록, 삭제 스윕 금지).
POSTLIST_PARTIAL_HTML = """\
<html><body>
<div class="post-list">
  <a href="/PostList.naver?blogId=going_tothe_moon&amp;logNo=224265571184">AI 인프라 기업의 독주 흐름</a>
  <a href="/PostList.naver?blogId=going_tothe_moon&amp;logNo=224265014460">ESS 투자기회</a>
</div>
</body></html>
"""

#: 목록이 스스로 표시한 상태(비공개·삭제) — 합성.
POSTLIST_MARKED_HTML = """\
<html><body>
<div class="post-list">
  <a href="/PostList.naver?blogId=going_tothe_moon&amp;logNo=224266000001"
     data-access="private">비공개</a>
  <a href="/PostList.naver?blogId=going_tothe_moon&amp;logNo=224266000002"
     data-withdrawn="true">삭제된 글</a>
  <a href="/PostList.naver?blogId=going_tothe_moon&amp;logNo=224266000003">정상 글</a>
  <a data-log-no="224266000004" data-date="2026-05-01">data 속성만 있는 글</a>
</div>
</body></html>
"""

#: 글이 없다고 스스로 선언한 목록 — 합성.
POSTLIST_EMPTY_DECLARED_HTML = """\
<html><body><div class="no-post">등록된 글이 없습니다</div></body></html>
"""

#: 마크업이 바뀐 것으로 보이는 응답(항목 0, 빈 선언도 없음) — 합성.
POSTLIST_STRUCTURE_CHANGED_HTML = """\
<html><body><div class="post-list"><div class="skeleton"></div></div></body></html>
"""


class _PageTestBase(unittest.TestCase):
    """공통: 변환기는 순수 함수이므로 픽스처 → 페이지 한 줄로 검증한다."""

    def page(self, html_text: str, **kwargs) -> ListingPage:
        return nbl.parse_post_list_html(html_text, blog_id=BLOG_ID, **kwargs)

    def ids(self, page: ListingPage) -> list[str]:
        return [entry.external_id for entry in page.entries]


# --------------------------------------------------------------------------- 1. 정책
class NoNetworkImportsTests(unittest.TestCase):
    """변환기가 수집 경로로 오용되지 않도록 **코드 수준에서** 막혀 있는지 확인한다."""

    FORBIDDEN_MODULES = ("import requests", "import urllib", "from urllib", "import http.client",
                         "from http", "import socket", "import httpx", "import aiohttp")
    #: dict/list 의 `.get(` 는 네트워크가 아니다 — **네트워크를 여는 호출**만 본다.
    FORBIDDEN_CALLS = ("urlopen(", "requests.get(", "requests.post(", "requests.Session(",
                       "httpx.", "aiohttp.", "socket.socket(", "http.client.", "urlretrieve(")

    def setUp(self) -> None:
        self.source = Path(nbl.__file__).read_text(encoding="utf-8")

    def test_module_has_no_network_imports(self):
        offenders = [token for token in self.FORBIDDEN_MODULES if token in self.source]
        self.assertEqual(
            offenders, [],
            f"목록 변환기에 네트워크 수입이 생겼다: {offenders} — 이 모듈은 순수 변환기여야 한다",
        )

    def test_module_never_opens_a_connection(self):
        offenders = [token for token in self.FORBIDDEN_CALLS if token in self.source]
        self.assertEqual(offenders, [], f"네트워크 호출 의심 토큰: {offenders}")

    def test_robots_status_is_disallowed_and_documented(self):
        self.assertEqual(nbl.ROBOTS_STATUS, "disallowed")
        self.assertEqual(nbl.NAVER_POST_LIST_PATH, "/PostList.naver")
        self.assertIn("Disallow: /PostList.naver", nbl.ROBOTS_EVIDENCE)
        self.assertIn("robots_disallowed", nbl.POLICY_NOTE)


class FixtureProvenanceTests(unittest.TestCase):
    """합성 픽스처를 '캡처 표본'으로 오독하지 않게 고정한다."""

    def test_provenance_is_synthetic(self):
        self.assertEqual(nbl.FIXTURE_PROVENANCE, "synthetic")

    def test_adapter_docstring_states_not_verified_against_live(self):
        doc = nbl.__doc__ or ""
        self.assertIn("수집 허가가 아니다", doc)
        self.assertIn("합성 픽스처", doc)


class RobotsContractTests(unittest.TestCase):
    """실측 robots 규칙을 우리 정책 계층으로 **다시 계산**해 차단을 고정한다.

    네트워크 접속 0 — 캡처한 robots 본문만 쓴다.
    """

    def setUp(self) -> None:
        self.rules = parse_robots(ROBOTS_TXT_CAPTURED)
        self.token = FetchPolicy(allowed_hosts=(nbl.NAVER_HOST,)).robots_user_agent_token

    def test_listing_path_is_blocked_for_our_group(self):
        self.assertFalse(self.rules.allows("/PostList.naver", product_token=self.token))

    def test_detail_path_is_allowed(self):
        path = urlsplit(nbl.detail_url(BLOG_ID, "224265571184")).path
        self.assertTrue(self.rules.allows(path, product_token=self.token))

    def test_group_selection_picks_star_group_for_us(self):
        """우리 토큰은 GPTBot 그룹에 걸리지 않고 `*` 그룹으로 판정돼야 한다.

        두 그룹이 섞이면 '전부 차단'이 되어 정책 판정이 무의미해진다 — 그룹 선택을 고정한다.
        """
        ours = self.rules.group_for(self.token)
        gpt = self.rules.group_for("GPTBot")
        self.assertIsNotNone(ours)
        self.assertIsNotNone(gpt)
        self.assertIsNot(ours, gpt)
        assert ours is not None  # 타입 검사기용 좁히기(위 assertIsNotNone 과 동일한 사실)
        self.assertIn("*", ours.agents)
        self.assertTrue(self.rules.allows("/going_tothe_moon/224265571184", product_token=self.token))


class AuthorizationTests(unittest.TestCase):
    """목록 URL 생성 자체가 승인 지점이다 — 승인 없이는 어떤 경로로도 URL 이 나오지 않는다."""

    def test_without_approval_always_raises(self):
        with self.assertRaises(nbl.PostListNotAuthorized) as ctx:
            nbl.build_listing_url(BLOG_ID)
        self.assertIn(nbl.AUTHORIZATION_REQUIRED_CODE, str(ctx.exception))

    def test_approval_without_postlist_flag_still_raises(self):
        approval = nbl.CollectionApproval(approved_by="owner", approval_ref="B3-pending")
        self.assertFalse(approval.allow_postlist_path)
        with self.assertRaises(nbl.PostListNotAuthorized):
            nbl.build_listing_url(BLOG_ID, category_no=7, approval=approval)

    def test_explicit_approval_builds_url(self):
        approval = nbl.CollectionApproval(
            approved_by="owner", approval_ref="B3-official-path", allow_postlist_path=True
        )
        url = nbl.build_listing_url(BLOG_ID, category_no=7, page=2, approval=approval)
        self.assertEqual(
            url,
            f"https://{nbl.NAVER_HOST}{nbl.NAVER_POST_LIST_PATH}"
            f"?blogId={BLOG_ID}&categoryNo=7&currentPage=2",
        )

    def test_approval_requires_evidence_fields(self):
        with self.assertRaises(ValueError):
            nbl.CollectionApproval(approved_by=" ", approval_ref="x")
        with self.assertRaises(ValueError):
            nbl.CollectionApproval(approved_by="owner", approval_ref="")

    def test_url_builder_rejects_hostile_blog_id(self):
        approval = nbl.CollectionApproval(approved_by="owner", approval_ref="ref", allow_postlist_path=True)
        for bad in ("a/b", "a?b", "a&b", "a=b", "  "):
            with self.assertRaises(ValueError):
                nbl.build_listing_url(bad, approval=approval)
        with self.assertRaises(ValueError):
            nbl.build_listing_url(BLOG_ID, category_no="7;drop", approval=approval)
        with self.assertRaises(ValueError):
            nbl.build_listing_url(BLOG_ID, page=0, approval=approval)

    def test_stored_detail_url_never_uses_listing_path(self):
        url = nbl.detail_url(BLOG_ID, "224265571184")
        self.assertNotIn(nbl.NAVER_POST_LIST_PATH, url)
        self.assertEqual(url, f"https://{nbl.NAVER_HOST}/{BLOG_ID}/224265571184")
        with self.assertRaises(ValueError):
            nbl.detail_url(BLOG_ID, "not-a-number")


# --------------------------------------------------------------------------- 2. 추출
class EntryExtractionTests(_PageTestBase):
    def test_extracts_entries_in_page_order_with_ids(self):
        page = self.page(POSTLIST_LAST_PAGE_HTML)
        self.assertEqual(self.ids(page), ["224265571184", "224265014460", "224264230264"])

    def test_titles_are_collapsed_and_nested_text_kept(self):
        page = self.page(POSTLIST_LAST_PAGE_HTML)
        self.assertEqual(page.entries[0].title, "AI 인프라 기업의 독주 흐름")
        # 인라인 태그로 쪼개진 조각은 이어 붙인다('ESS' + '가 …') — 공백을 지어내지 않는다.
        self.assertEqual(page.entries[1].title, "다음 투자기회는 ESS가 될 것입니다")

    def test_entry_url_is_detail_path(self):
        page = self.page(POSTLIST_LAST_PAGE_HTML)
        for entry in page.entries:
            self.assertEqual(entry.url, f"https://{nbl.NAVER_HOST}/{BLOG_ID}/{entry.external_id}")

    def test_duplicate_log_no_appears_once(self):
        html_text = (
            '<a href="/PostList.naver?logNo=224265571184">A</a>'
            '<a href="/PostList.naver?logNo=224265571184">A (중복 렌더)</a>'
            '<a href="/PostList.naver?logNo=224265014460">B</a>'
        )
        page = self.page(html_text)
        self.assertEqual(self.ids(page), ["224265571184", "224265014460"])

    def test_non_post_anchors_are_ignored(self):
        html_text = (
            '<a href="/PostList.naver?blogId=going_tothe_moon&categoryNo=7">카테고리</a>'
            '<a href="/PostList.naver?logNo=123456">너무 짧은 번호</a>'
            '<a href="/PostList.naver?logNo=224265571184">정상 글</a>'
        )
        page = self.page(html_text)
        self.assertEqual(self.ids(page), ["224265571184"])

    def test_data_attribute_log_no_supported(self):
        page = self.page(POSTLIST_MARKED_HTML)
        self.assertIn("224266000004", self.ids(page))

    def test_empty_anchor_text_gives_none_title(self):
        page = self.page('<a href="/PostList.naver?logNo=224265571184"></a>')
        self.assertIsNone(page.entries[0].title)

    def test_bytes_input_is_rejected(self):
        with self.assertRaises(TypeError) as ctx:
            # 타입 검사기가 막는 것이 정상이다(의도된 음성 케이스) — 런타임 계약을 확인한다.
            nbl.parse_post_list_html(POSTLIST_LAST_PAGE_HTML.encode("utf-8"), blog_id=BLOG_ID)  # type: ignore[arg-type]
        self.assertIn(nbl.REASON_NOT_TEXT, str(ctx.exception))

    def test_empty_html_is_parse_error_not_zero_entries(self):
        with self.assertRaises(nbl.PostListParseError) as ctx:
            self.page("   \n  ")
        self.assertEqual(ctx.exception.code, nbl.REASON_EMPTY_HTML)

    def test_structure_change_is_parse_error_not_zero_entries(self):
        """마크업이 바뀐 응답을 '글 0건'으로 반환하면 삭제 스윕의 근거가 오염된다."""
        with self.assertRaises(nbl.PostListParseError) as ctx:
            self.page(POSTLIST_STRUCTURE_CHANGED_HTML)
        self.assertEqual(ctx.exception.code, nbl.REASON_NO_ENTRIES)

    def test_declared_empty_listing_returns_empty_partial_page(self):
        """목록이 '글이 없다'고 스스로 말한 경우만 0건으로 인정한다 — 그래도 부분 목록이다."""
        page = self.page(POSTLIST_EMPTY_DECLARED_HTML)
        self.assertEqual(page.entries, ())
        self.assertFalse(page.complete)
        self.assertFalse(page.tombstones_allowed)
        self.assertEqual(page.coverage, "denominator_unknown")

    def test_undeclared_empty_requires_explicit_opt_in(self):
        """빈 선언도 없고 항목도 없으면 구조 변경과 구분 불가 → 기본은 예외, opt-in 시에만 0건."""
        with self.assertRaises(nbl.PostListParseError) as ctx:
            self.page(POSTLIST_STRUCTURE_CHANGED_HTML)
        self.assertEqual(ctx.exception.code, nbl.REASON_NO_ENTRIES)

        page = self.page(POSTLIST_STRUCTURE_CHANGED_HTML, allow_empty=True)
        self.assertEqual(page.entries, ())
        self.assertFalse(page.complete)
        self.assertFalse(page.tombstones_allowed)


class PublishedAtTests(unittest.TestCase):
    def test_dotted_and_korean_dates_normalized(self):
        self.assertEqual(nbl.normalize_published_at("2026. 4. 26."), "2026-04-26")
        self.assertEqual(nbl.normalize_published_at("2026.04.25"), "2026-04-25")
        self.assertEqual(nbl.normalize_published_at("2026년 4월 26일"), "2026-04-26")
        self.assertEqual(nbl.normalize_published_at("2026/04/26"), "2026-04-26")

    def test_time_component_kept(self):
        self.assertEqual(nbl.normalize_published_at("2026-04-26 14:05"), "2026-04-26 14:05:00")
        self.assertEqual(nbl.normalize_published_at("2026-04-26T14:05:09"), "2026-04-26 14:05:09")

    def test_unparsable_gives_none_never_a_guess(self):
        for raw in (None, "", "어제", "방금", "2026", "13월 40일 2026"):
            self.assertIsNone(nbl.normalize_published_at(raw), f"{raw!r} 는 None 이어야 한다")

    def test_missing_date_in_listing_becomes_none(self):
        page = nbl.parse_post_list_html(
            '<a href="/PostList.naver?logNo=224265571184">날짜 없는 글</a>', blog_id=BLOG_ID
        )
        self.assertIsNone(page.entries[0].published_at)


# --------------------------------------------------------------------------- 3. 완전성
class CompletenessTests(_PageTestBase):
    def test_last_page_marker_makes_listing_complete(self):
        page = self.page(POSTLIST_LAST_PAGE_HTML)
        self.assertTrue(page.complete)
        self.assertFalse(page.truncated)
        self.assertTrue(page.tombstones_allowed)
        self.assertEqual(page.coverage, "known")
        self.assertEqual(page.limitations, [])

    def test_more_pages_marker_marks_truncated_with_reason(self):
        page = self.page(POSTLIST_MORE_PAGES_HTML)
        self.assertFalse(page.complete)
        self.assertTrue(page.truncated)
        self.assertEqual(page.limitation, nbl.LIMITATION_MORE_PAGES)
        self.assertFalse(page.tombstones_allowed)
        self.assertEqual(page.coverage, "denominator_unknown")
        self.assertEqual(page.limitations, [f"listing_truncated:{nbl.LIMITATION_MORE_PAGES}"])

    def test_no_marker_stays_partial_and_blocks_sweep(self):
        page = self.page(POSTLIST_PARTIAL_HTML)
        self.assertFalse(page.complete)
        self.assertFalse(page.truncated)
        self.assertFalse(page.tombstones_allowed)
        self.assertIn("listing_partial", page.limitations[0])

    def test_truncating_entries_never_claims_complete(self):
        """항목을 잘라냈는데 '완전'이라 주장하면 거짓 삭제(tombstone)가 만들어진다."""
        page = self.page(POSTLIST_LAST_PAGE_HTML, max_entries=2)
        self.assertEqual(len(page.entries), 2)
        self.assertFalse(page.complete)
        self.assertTrue(page.truncated)
        self.assertEqual(page.limitation, nbl.LIMITATION_ENTRY_LIMIT)
        self.assertFalse(page.tombstones_allowed)

    def test_both_markers_prefers_truncated_over_complete(self):
        html_text = (
            '<a href="/PostList.naver?logNo=224265571184">A</a>'
            '{"isLastPage": true, "hasNextPage": true}'
        )
        page = self.page(html_text)
        self.assertFalse(page.complete)
        self.assertTrue(page.truncated)

    def test_adapter_would_skip_sweep_for_partial_page(self):
        """어댑터의 삭제 스윕 조건과 같은 술어를 쓴다는 것을 고정한다."""
        partial = self.page(POSTLIST_PARTIAL_HTML)
        complete = self.page(POSTLIST_LAST_PAGE_HTML)
        self.assertFalse(partial.tombstones_allowed)
        self.assertTrue(complete.tombstones_allowed)


# --------------------------------------------------------------------------- 4. 권한/삭제 표시
class PermissionMarkerTests(_PageTestBase):
    def setUp(self) -> None:
        self.page_ = self.page(POSTLIST_MARKED_HTML)
        self.by_id = {entry.external_id: entry for entry in self.page_.entries}

    def test_listing_marked_private_is_flagged(self):
        self.assertTrue(self.by_id["224266000001"].is_private)
        self.assertFalse(self.by_id["224266000001"].withdrawn)

    def test_listing_marked_withdrawn_is_flagged(self):
        self.assertTrue(self.by_id["224266000002"].withdrawn)
        self.assertFalse(self.by_id["224266000002"].is_private)

    def test_unmarked_entries_are_not_guessed(self):
        entry = self.by_id["224266000003"]
        self.assertFalse(entry.is_private)
        self.assertFalse(entry.withdrawn)

    def test_title_marker_does_not_override_explicit_flag(self):
        withdrawn, is_private = nbl.classify_entry({"title": "비공개", "access": "public"})
        self.assertFalse(withdrawn)
        self.assertTrue(is_private)

    def test_flags_do_not_leak_between_entries(self):
        self.assertFalse(self.by_id["224266000004"].is_private)
        self.assertFalse(self.by_id["224266000004"].withdrawn)


# --------------------------------------------------------------------------- 5. 어댑터 연결
class AdapterContractTests(_PageTestBase):
    def test_output_is_listing_page_consumed_by_adapter(self):
        page = self.page(POSTLIST_LAST_PAGE_HTML)
        self.assertIsInstance(page, ListingPage)
        from agi.adapters.blog import BlogAdapter

        params = inspect.signature(BlogAdapter.ingest_page).parameters
        self.assertIn("page", params)
        # 변환기 출력이 곧 어댑터 입력 타입이라는 것을 주석 대신 코드로 확인한다.
        self.assertEqual(params["page"].annotation, "ListingPage")

    def test_entries_carry_only_contract_fields(self):
        entry = self.page(POSTLIST_LAST_PAGE_HTML).entries[0]
        self.assertEqual(
            set(vars(entry)),
            {"external_id", "title", "url", "published_at", "language", "withdrawn", "is_private"},
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
