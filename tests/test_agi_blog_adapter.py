"""AGI-03 블로그 어댑터 회귀 테스트 — 발견·revision·삭제·권한·검색 한계.

계약 정본: `docs/AGI_01_SOURCE_TIME_AUTH_CONTRACT_20261011.md`
  * §5-2 revision·tombstone(행을 지우지 않는다, 권한 오류를 삭제로 기록 금지)
  * §6   완전성(분모 미확인) 표기
  * §9-4 노출 경계(공개 allowlist + 살아 있는 글만)
  * 수용기준 T2(개정/삭제) · T8(완전성)

실행:
    ./venv/bin/python -m pytest tests/test_agi_blog_adapter.py -v

범위(Planner 지시 2026-10-11): **fixture / 임시 스키마 한정**.
네트워크 호출·라이브 수집·legacy 테이블 쓰기 없음 — `fetch` 는 fixture 콜러블뿐이다.
"""

from __future__ import annotations

import unittest
from unittest import mock

from agi import repository
from agi.adapters.blog import (
    ADAPTER_VERSION,
    BLOG_SCOPE,
    REASON_ABSENT_FROM_COMPLETE_LISTING,
    REASON_FETCH_GONE,
    REASON_FETCH_RESTRICTED,
    REASON_LISTING_MARKED_PRIVATE,
    REASON_LISTING_MARKED_WITHDRAWN,
    BlogAdapter,
    FetchResult,
    ListingEntry,
    ListingPage,
    PostDetail,
    classify_fetch_status,
    fixture_fetch,
    parse_detail,
    parse_listing,
)
from agi.repository import (
    INTERNAL_VISIBILITY,
    PUBLIC_SCOPES,
    PUBLIC_VISIBILITY,
    ResearchRepository,
    make_document_id,
    public_scope_clause,
)
from agi.timeutil import now_utc
from tests.agi_test_support import StatementRecorder, isolated_schema

LEGACY_TABLES = ("telegram_channel_posts", "telegram_channels")
BLOG_KEY = "demo-blog"


# --------------------------------------------------------------------------- 픽스처
def _listing(ids, *, complete=False, truncated=False, limitation=None, **flags):
    """(external_id, ...) 목록으로 ListingPage 를 만든다. flags 는 id→속성 dict.

    기본값이 `complete=False`(부분 목록)인 것은 의도적이다 — 목록 완전성은 **주장해야
    하는 사실**이므로, 완전성을 주장하는 호출부가 `complete=True` 를 명시하게 만든다.
    """
    entries = tuple(
        ListingEntry(
            external_id=str(external_id),
            title=f"글 {external_id}",
            url=f"https://demo.blog/{external_id}",
            published_at="2026-10-09 08:30:00",
            **flags.get(str(external_id), {}),
        )
        for external_id in ids
    )
    return ListingPage(
        entries=entries, complete=complete, truncated=truncated, limitation=limitation
    )


def _details(mapping):
    """external_id → 본문 fixture. 값이 dict 면 상세 payload, int 면 HTTP 상태."""
    payload = {}
    for external_id, value in mapping.items():
        if isinstance(value, int):
            payload[str(external_id)] = {"http_status": value}
        else:
            payload[str(external_id)] = {"body_text": value}
    return fixture_fetch(payload)


def _setup(
    conn, schema, *, blog_key=BLOG_KEY, scope=BLOG_SCOPE, publish_summary=None, store_original="yes"
):
    """기본 `store_original="yes"` — 이 모듈 대부분의 테스트는 본문 보관 자체가 아니라
    발견·revision·삭제·권한을 검증한다. 보관 권한(R1) 전용 테스트는 명시적으로
    `store_original="metadata_only"`/`"no"` 를 넘긴다(계약 §7, 기본 DB 값은
    `metadata_only` 다 — 테스트는 이를 명시적으로 올려 기존 수용기준과 섞이지 않게 한다).
    """
    adapter = BlogAdapter(schema=schema, host="test-host")
    repo = ResearchRepository(conn, schema=schema)
    source_id = repo.ensure_source(
        source_kind="blog",
        source_owner="owner",
        source_native_id=blog_key,
        display_name="데모 블로그",
        scope=scope,
    )
    conn.execute(
        f'UPDATE "{schema}".research_sources SET store_original = %s WHERE source_id = %s',
        (store_original, source_id),
    )
    if publish_summary is not None:
        conn.execute(
            f'UPDATE "{schema}".research_sources SET publish_summary = %s WHERE source_id = %s',
            (publish_summary, source_id),
        )
    conn.commit()
    return adapter, repo, source_id


# --------------------------------------------------------------------------- 순수 로직
class FetchStatusClassificationTests(unittest.TestCase):
    """R2 — 권한 오류(401/403)를 삭제로 뭉개지 않는다."""

    def test_200_is_ok(self) -> None:
        self.assertEqual(classify_fetch_status(200), ("ok", None))

    def test_permission_errors_are_restricted_not_deleted(self) -> None:
        for status in (401, 403):
            state, reason = classify_fetch_status(status)
            self.assertEqual(state, "restricted", f"{status} 이 삭제로 분류되었다")
            self.assertNotEqual(state, "deleted")
            self.assertEqual(reason, REASON_FETCH_RESTRICTED)

    def test_gone_statuses_are_deleted(self) -> None:
        for status in (404, 410):
            self.assertEqual(classify_fetch_status(status), ("deleted", REASON_FETCH_GONE))

    def test_transient_failures_are_unavailable(self) -> None:
        for status in (429, 500, 502, 503, 504):
            self.assertEqual(classify_fetch_status(status)[0], "unavailable")

    def test_restricted_and_deleted_reasons_differ(self) -> None:
        """두 사유 코드가 같아지면 원장에서 권한 오류와 삭제를 구분할 수 없다."""
        self.assertNotEqual(classify_fetch_status(403)[1], classify_fetch_status(404)[1])

    def test_unmapped_status_raises_instead_of_guessing(self) -> None:
        with self.assertRaises(ValueError):
            classify_fetch_status(302)
        with self.assertRaises(ValueError):
            classify_fetch_status(418)


class ListingPageContractTests(unittest.TestCase):
    """R3 — 불완전한 목록으로 삭제를 추론하지 않는다(fail-closed)."""

    def test_partial_listing_forbids_tombstone_sweep(self) -> None:
        page = _listing([1, 2])
        self.assertFalse(page.tombstones_allowed)
        self.assertEqual(page.coverage, "denominator_unknown")
        self.assertTrue(page.limitations, "부분 목록인데 한계 표시가 없다")

    def test_truncated_listing_requires_a_limitation_note(self) -> None:
        with self.assertRaises(ValueError):
            ListingPage(entries=(ListingEntry(external_id="1"),), truncated=True)

    def test_truncated_listing_carries_its_note(self) -> None:
        page = _listing([1], truncated=True, limitation="검색 결과 1페이지까지만 노출")
        self.assertFalse(page.tombstones_allowed)
        self.assertIn("listing_truncated", page.limitations[0])
        self.assertIn("검색 결과 1페이지까지만 노출", page.limitations[0])

    def test_complete_and_truncated_cannot_both_hold(self) -> None:
        with self.assertRaises(ValueError):
            ListingPage(
                entries=(ListingEntry(external_id="1"),), complete=True, truncated=True, limitation="x"
            )

    def test_complete_listing_allows_sweep_and_reports_known_coverage(self) -> None:
        page = _listing([1, 2], complete=True)
        self.assertTrue(page.tombstones_allowed)
        self.assertEqual(page.coverage, "known")
        self.assertEqual(page.limitations, [])

    def test_entry_requires_external_id(self) -> None:
        with self.assertRaises(ValueError):
            ListingEntry(external_id="  ")


class FetchResultContractTests(unittest.TestCase):
    def test_200_requires_detail(self) -> None:
        with self.assertRaises(ValueError):
            FetchResult(http_status=200)

    def test_error_status_must_not_carry_a_body(self) -> None:
        """권한 오류 응답의 본문을 원문으로 승격하지 않는다."""
        with self.assertRaises(ValueError):
            FetchResult(http_status=403, detail=PostDetail(body_text="비공개 본문"))


class FixtureParsingTests(unittest.TestCase):
    def test_parse_listing_and_detail(self) -> None:
        page = parse_listing(
            {
                "entries": [
                    {"external_id": 7, "title": "제목", "published_at": "2026-10-09 08:30:00"},
                    {"external_id": 8, "withdrawn": True},
                ],
                "complete": True,
            }
        )
        self.assertEqual([entry.external_id for entry in page.entries], ["7", "8"])
        self.assertTrue(page.entries[1].withdrawn)
        self.assertFalse(page.entries[0].withdrawn)

        detail = parse_detail({"body_text": "본문", "media_refs": ["a.jpg"]})
        self.assertEqual(detail.media_list, ["a.jpg"])

    def test_fixture_fetch_returns_404_for_unknown_ids(self) -> None:
        fetch = _details({1: "본문"})
        self.assertEqual(fetch("1").http_status, 200)
        self.assertEqual(fetch("999").http_status, 404)

    def test_fixture_fetch_media_refs_are_sorted_and_deduped(self) -> None:
        fetch = fixture_fetch({"1": {"body_text": "본문", "media_refs": ["b.jpg", "a.jpg", "a.jpg"]}})
        self.assertEqual(fetch("1").detail.media_list, ["a.jpg", "b.jpg"])


# --------------------------------------------------------------------------- DB 통합
class AdapterIngestionTests(unittest.TestCase):
    """수집·멱등·revision (임시 스키마에서 실제 PG DDL/DML 로 실행)."""

    def test_complete_listing_stores_documents_and_revisions(self) -> None:
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema)
            result = adapter.ingest_page(conn, source_id, _listing([1, 2, 3], complete=True), _details({
                1: "본문 하나", 2: "본문 둘", 3: "본문 셋",
            }))
            conn.commit()

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["listed_count"], 3)
            self.assertEqual(result["stored_count"], 3)
            self.assertEqual(result["skipped_duplicate"], 0)
            self.assertTrue(result["tombstone_sweep_performed"])
            self.assertEqual(result["coverage"], "known")
            self.assertEqual(result["adapter"], ADAPTER_VERSION)

            fingerprint = repo.snapshot_fingerprint()
            self.assertEqual(fingerprint["documents"], 3)
            self.assertEqual(fingerprint["revisions"], 3)
            self.assertEqual(fingerprint["runs"], 1)

            source = repo.get_source(source_id)
            self.assertEqual(source["scope"], BLOG_SCOPE)
            self.assertEqual(source["listed_count"], 3)
            self.assertEqual(source["stored_count"], 3)
            self.assertEqual(source["coverage"], "known")
            self.assertIsNotNone(source["page_cursor"])

    def test_reingest_is_idempotent(self) -> None:
        """R5 — 재실행은 엔티티 추가 0, 실행 원장만 1행."""
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema)
            page = _listing([1, 2], complete=True)
            details = _details({1: "A", 2: "B"})

            adapter.ingest_page(conn, source_id, page, details)
            conn.commit()
            before = repo.snapshot_fingerprint()

            second = adapter.ingest_page(conn, source_id, page, details)
            conn.commit()
            after = repo.snapshot_fingerprint()

            self.assertEqual((after["documents"], after["revisions"]), (before["documents"], before["revisions"]))
            self.assertEqual(after["runs"], before["runs"] + 1, "실행 원장이 늘지 않았다")
            self.assertEqual(second["stored_count"], 0)
            self.assertEqual(second["skipped_duplicate"], 2)

    def test_body_edit_adds_one_revision_and_keeps_the_old_one(self) -> None:
        """수용기준 T2 — 본문 수정 → revision 1개 추가, 기존 revision 불변."""
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema)
            page = _listing([1], complete=True)

            adapter.ingest_page(conn, source_id, page, _details({1: "원문 A"}))
            conn.commit()
            document_id = make_document_id(source_id, "1")
            first = repo.list_revisions(document_id)

            adapter.ingest_page(conn, source_id, page, _details({1: "원문 B"}))
            conn.commit()
            revisions = repo.list_revisions(document_id)

            self.assertEqual(len(revisions), 2)
            self.assertEqual(revisions[1]["revision_no"], 2)
            self.assertEqual(revisions[1]["change_kind"], "text_edit")
            self.assertEqual(revisions[0]["revision_hash"], first[0]["revision_hash"])
            self.assertEqual(revisions[0]["text_digest"], first[0]["text_digest"])
            self.assertEqual(
                repo.get_document(document_id, visibility=INTERNAL_VISIBILITY)["current_revision_no"], 2
            )

    def test_media_only_change_is_not_a_new_revision(self) -> None:
        """R6 — 해시 범위가 본문 전용이므로 미디어만 바뀐 글은 새 revision 이 아니다.

        대신 그 변화는 `media_refs`(선언 참조)에 남는다 — 해시에 들어가지 않으므로
        revision 이 될 수 없고, 미디어 바이트 해시는 AGI-04 인수 항목이다.
        """
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema)
            page = _listing([1], complete=True)
            document_id = make_document_id(source_id, "1")

            adapter.ingest_page(
                conn, source_id, page, fixture_fetch({"1": {"body_text": "같은 본문", "media_refs": ["a.jpg"]}})
            )
            conn.commit()
            self.assertEqual(len(repo.list_revisions(document_id)), 1)

            second = adapter.ingest_page(
                conn,
                source_id,
                page,
                fixture_fetch({"1": {"body_text": "같은 본문", "media_refs": ["b.jpg", "c.jpg"]}}),
            )
            conn.commit()

            self.assertEqual(len(repo.list_revisions(document_id)), 1, "미디어만 바뀌었는데 revision 이 늘었다")
            self.assertEqual(second["stored_count"], 0)
            self.assertEqual(second["skipped_duplicate"], 1)
            document = repo.get_document(document_id, visibility=INTERNAL_VISIBILITY)
            self.assertEqual(sorted(document["media_refs"].split("\n")), ["b.jpg", "c.jpg"])
            self.assertEqual(document["attachment_count"], 0)

    def test_unknown_source_raises(self) -> None:
        with isolated_schema() as (conn, schema):
            adapter = BlogAdapter(schema=schema)
            with self.assertRaises(KeyError):
                adapter.ingest_page(conn, "0" * 64, _listing([1], complete=True), _details({1: "A"}))

    def test_adapter_refuses_a_non_blog_source(self) -> None:
        """R7 — 어댑터 교차 오용을 조용히 넘기지 않는다."""
        with isolated_schema() as (conn, schema):
            adapter, _repo, source_id = _setup(conn, schema, blog_key="tg-chan", scope="public_channel")
            with self.assertRaises(ValueError):
                adapter.ingest_page(conn, source_id, _listing([1], complete=True), _details({1: "A"}))

    def test_fetch_failure_is_recorded_and_reraised(self) -> None:
        """행 단위 실패를 숨기지 않는다 — 원장은 failed, 커서는 전진하지 않는다."""
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema)

            def failing_fetch(external_id):
                if external_id == "2":
                    raise RuntimeError("boom")
                return FetchResult(http_status=200, detail=PostDetail(body_text="A"))

            with self.assertRaises(RuntimeError):
                adapter.ingest_page(conn, source_id, _listing([1, 2, 3], complete=True), failing_fetch)
            conn.rollback()

            runs = repo.list_runs(source_id=source_id)
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["status"], "failed")
            self.assertIn("boom", runs[0]["error"])
            self.assertIsNone(runs[0]["cursor_end"])
            self.assertIsNone(repo.get_source(source_id)["page_cursor"], "실패했는데 커서가 전진했다")

    def test_transient_failure_marks_the_run_partial(self) -> None:
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema)
            result = adapter.ingest_page(
                conn, source_id, _listing([1, 2], complete=True), _details({1: "A", 2: 503})
            )
            conn.commit()
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["skipped_unavailable"], 1)
            self.assertEqual(result["stored_count"], 1)
            source = repo.get_source(source_id)
            self.assertEqual(source["skipped_error"], 1, "접근 불가 건수가 소스 누적에 반영되지 않았다")


class AdapterDeletionTests(unittest.TestCase):
    """R1·R3·R4 — 삭제는 tombstone(행 삭제 아님), 추론은 완전한 목록에서만."""

    def test_withdrawn_entry_is_tombstoned_even_from_a_partial_listing(self) -> None:
        """R4 — 그 글 자체에 대한 증거는 목록 완전성과 무관하게 인정한다."""
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema)
            page = _listing([1], complete=False, **{"1": {"withdrawn": True}})
            result = adapter.ingest_page(conn, source_id, page, _details({}))

            document_id = make_document_id(source_id, "1")
            document = repo.get_document(document_id, visibility=INTERNAL_VISIBILITY)
            self.assertEqual(document["access_state"], "deleted")
            self.assertEqual(document["tombstone_reason"], REASON_LISTING_MARKED_WITHDRAWN)
            self.assertIsNotNone(document["tombstoned_at"])
            self.assertEqual(len(repo.list_revisions(document_id)), 0, "삭제 글로 revision 을 만들었다")
            self.assertEqual(result["tombstones_from_listing"], 1)
            self.assertFalse(result["tombstone_sweep_performed"])

    def test_absent_post_is_tombstoned_from_a_complete_listing_only(self) -> None:
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema)
            adapter.ingest_page(conn, source_id, _listing([1, 2, 3], complete=True), _details({
                1: "A", 2: "B", 3: "C",
            }))
            conn.commit()
            removed_id = make_document_id(source_id, "2")

            result = adapter.ingest_page(
                conn, source_id, _listing([1, 3], complete=True), _details({1: "A", 3: "C"})
            )
            conn.commit()

            self.assertTrue(result["tombstone_sweep_performed"])
            self.assertEqual(result["tombstones_from_sweep"], 1)
            self.assertEqual(result["skipped_deleted"], 1)
            document = repo.get_document(removed_id, visibility=INTERNAL_VISIBILITY)
            self.assertEqual(document["access_state"], "deleted")
            self.assertEqual(document["tombstone_reason"], REASON_ABSENT_FROM_COMPLETE_LISTING)
            self.assertEqual(
                len(repo.list_revisions(removed_id)), 1, "삭제로 revision 이 사라졌다(계약 §5-2 위반)"
            )
            # 살아 있는 글은 건드리지 않는다.
            self.assertEqual(
                repo.get_document(make_document_id(source_id, "1"), visibility=INTERNAL_VISIBILITY)[
                    "access_state"
                ],
                "ok",
            )

    def test_partial_listing_never_tombstones_existing_documents(self) -> None:
        """R3 하중 검증 — 부분 목록으로 스윕하면 살아 있는 글이 대량 삭제된다."""
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema)
            adapter.ingest_page(conn, source_id, _listing([1, 2, 3], complete=True), _details({
                1: "A", 2: "B", 3: "C",
            }))
            conn.commit()

            partial = _listing(
                [1], complete=False, truncated=True, limitation="검색 결과 잘림"
            )
            result = adapter.ingest_page(conn, source_id, partial, _details({1: "A"}))
            conn.commit()

            self.assertFalse(result["tombstone_sweep_performed"])
            self.assertEqual(result["skipped_deleted"], 0)
            self.assertTrue(
                any("tombstone_sweep_skipped" in note for note in result["limitations"]),
                "스윕을 건너뛴 사실이 결과에 없다 — '삭제 0건'이 거짓 근거가 된다",
            )
            for external_id in ("1", "2", "3"):
                document = repo.get_document(
                    make_document_id(source_id, external_id), visibility=INTERNAL_VISIBILITY
                )
                self.assertEqual(document["access_state"], "ok", f"{external_id} 가 잘못 삭제되었다")
            self.assertEqual(repo.get_source(source_id)["coverage"], "denominator_unknown")

    def test_resurrection_returns_a_document_to_ok_without_losing_history(self) -> None:
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema)
            adapter.ingest_page(conn, source_id, _listing([1, 2], complete=True), _details({1: "A", 2: "B"}))
            conn.commit()
            adapter.ingest_page(conn, source_id, _listing([1], complete=True), _details({1: "A"}))
            conn.commit()
            document_id = make_document_id(source_id, "2")
            self.assertEqual(
                repo.get_document(document_id, visibility=INTERNAL_VISIBILITY)["access_state"], "deleted"
            )

            result = adapter.ingest_page(
                conn, source_id, _listing([1, 2], complete=True), _details({1: "A", 2: "B"})
            )
            conn.commit()

            document = repo.get_document(document_id, visibility=INTERNAL_VISIBILITY)
            self.assertEqual(document["access_state"], "ok")
            self.assertIsNone(document["tombstoned_at"])
            self.assertIsNone(document["tombstone_reason"])
            self.assertEqual(result["resurrected_count"], 1)
            self.assertEqual(len(repo.list_revisions(document_id)), 1, "되돌림이 revision 을 중복 생성했다")

    def test_repeated_sweep_does_not_recount_already_tombstoned_documents(self) -> None:
        """멱등성: 삭제 0건이 '이미 삭제됨'을 다시 세면 실행 요약이 사실과 달라진다."""
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema)
            adapter.ingest_page(conn, source_id, _listing([1, 2], complete=True), _details({1: "A", 2: "B"}))
            conn.commit()
            adapter.ingest_page(conn, source_id, _listing([1], complete=True), _details({1: "A"}))
            conn.commit()

            again = adapter.ingest_page(conn, source_id, _listing([1], complete=True), _details({1: "A"}))
            conn.commit()
            self.assertEqual(again["tombstones_from_sweep"], 0)
            self.assertEqual(again["skipped_deleted"], 0)
            self.assertEqual(again["tombstone_sweep_skipped_count"], 1)


class AdapterPermissionTests(unittest.TestCase):
    """R2 — 접근 불가를 삭제로 기록하지 않는다."""

    def test_403_is_recorded_as_restricted_and_keeps_the_stored_revision(self) -> None:
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema)
            page = _listing([1], complete=True)
            document_id = make_document_id(source_id, "1")

            adapter.ingest_page(conn, source_id, page, _details({1: "처음엔 읽혔다"}))
            conn.commit()

            result = adapter.ingest_page(conn, source_id, page, _details({1: 403}))
            conn.commit()

            document = repo.get_document(document_id, visibility=INTERNAL_VISIBILITY)
            self.assertEqual(document["access_state"], "restricted", "권한 오류가 삭제로 기록되었다")
            self.assertEqual(document["tombstone_reason"], REASON_FETCH_RESTRICTED)
            self.assertNotEqual(document["access_state"], "deleted")
            self.assertEqual(result["skipped_restricted"], 1)
            self.assertEqual(result["skipped_deleted"], 0)
            self.assertEqual(len(repo.list_revisions(document_id)), 1, "권한 오류로 revision 이 사라졌다")
            self.assertEqual(
                repo.list_revisions(document_id)[0]["body_text"], "처음엔 읽혔다"
            )

    def test_404_is_recorded_as_deleted(self) -> None:
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema)
            page = _listing([1], complete=True)
            adapter.ingest_page(conn, source_id, page, _details({1: "본문"}))

            result = adapter.ingest_page(conn, source_id, page, _details({1: 404}))
            conn.commit()

            document = repo.get_document(
                make_document_id(source_id, "1"), visibility=INTERNAL_VISIBILITY
            )
            self.assertEqual(document["access_state"], "deleted")
            self.assertEqual(document["tombstone_reason"], REASON_FETCH_GONE)
            self.assertEqual(result["tombstones_from_listing"], 0, "sweep 이 아닌 fetch 증거다")

    def test_private_post_is_restricted_and_its_body_is_never_fetched(self) -> None:
        """비공개 글의 본문을 원문 보관 테이블로 끌어오지 않는다(fail-closed)."""
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema)
            calls: list[str] = []

            def fetch(external_id):
                calls.append(external_id)
                return FetchResult(http_status=200, detail=PostDetail(body_text="비공개 본문"))

            page = _listing([1, 2], complete=True, **{"2": {"is_private": True}})
            adapter.ingest_page(conn, source_id, page, fetch)
            conn.commit()

            self.assertEqual(calls, ["1"], "비공개 글의 본문을 가져왔다")
            document_id = make_document_id(source_id, "2")
            document = repo.get_document(document_id, visibility=INTERNAL_VISIBILITY)
            self.assertEqual(document["access_state"], "restricted")
            self.assertEqual(document["tombstone_reason"], REASON_LISTING_MARKED_PRIVATE)
            self.assertTrue(document["is_private"])
            self.assertEqual(len(repo.list_revisions(document_id)), 0)

    def test_private_sources_raise_before_writing(self) -> None:
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema, blog_key="secret", scope="private_chat")
            with self.assertRaises(ValueError):
                adapter.ingest_page(conn, source_id, _listing([1], complete=True), _details({1: "A"}))


class AdapterExposureTests(unittest.TestCase):
    """§9-4 — tombstone 된 글은 공개 경로에 남지 않고, 계보는 관리자 경로에 남는다."""

    def test_deleted_and_restricted_documents_leave_the_public_view(self) -> None:
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema, publish_summary="owner_approval")
            adapter.ingest_page(conn, source_id, _listing([1, 2, 3, 4], complete=True), _details({
                1: "살아있는 글", 2: "삭제될 글", 3: "제한될 글", 4: 403,
            }))
            conn.commit()
            # 2 → 목록에서 사라져 tombstone, 3·4 → 접근 불가
            adapter.ingest_page(conn, source_id, _listing([1, 3, 4], complete=True), _details({
                1: "살아있는 글", 3: 403, 4: 403,
            }))
            conn.commit()

            public_ids = {row["document_id"] for row in repo.list_documents(visibility=PUBLIC_VISIBILITY, limit=500)}
            internal_ids = {
                row["document_id"] for row in repo.list_documents(visibility=INTERNAL_VISIBILITY, limit=500)
            }
            alive = make_document_id(source_id, "1")
            self.assertEqual(public_ids, {alive}, "tombstone 된 글이 공개 목록에 남았다")
            self.assertEqual(len(internal_ids), 4, "관리자 경로에서 계보가 사라졌다")

            for external_id in ("2", "3", "4"):
                document_id = make_document_id(source_id, external_id)
                self.assertIsNone(
                    repo.get_document(document_id, visibility=PUBLIC_VISIBILITY),
                    f"{external_id} 의 상세가 공개 경로에 남았다",
                )
                self.assertIsNotNone(
                    repo.get_document(document_id, visibility=INTERNAL_VISIBILITY),
                    f"{external_id} 의 상세가 관리자 경로에서도 사라졌다",
                )

    def test_blog_scope_is_publishable_once_permission_is_raised(self) -> None:
        """양성 대조 — 0건이 '규칙'인지 '데이터 없음'인지 구분한다."""
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema)  # 기본값 publish_summary='denied'
            adapter.ingest_page(conn, source_id, _listing([1], complete=True), _details({1: "본문"}))
            conn.commit()
            self.assertIn(BLOG_SCOPE, PUBLIC_SCOPES, "blog 는 공개 허용 scope 여야 한다")
            self.assertEqual(repo.list_documents(visibility=PUBLIC_VISIBILITY), [], "기본 deny 인데 공개되었다")

            conn.execute(
                f'UPDATE "{schema}".research_sources SET publish_summary = %s WHERE source_id = %s',
                ("owner_approval", source_id),
            )
            conn.commit()
            visible = repo.list_documents(visibility=PUBLIC_VISIBILITY, limit=500)
            self.assertEqual([row["document_id"] for row in visible], [make_document_id(source_id, "1")])

    def test_public_visibility_clause_pins_access_state(self) -> None:
        """노출 조건에 '살아 있는 글' 이 실제로 들어 있는지 문장으로 고정한다."""
        from agi.repository import document_visibility_clauses

        clauses = document_visibility_clauses(PUBLIC_VISIBILITY)
        self.assertIn("d.access_state = 'ok'", clauses)
        self.assertEqual(document_visibility_clauses(INTERNAL_VISIBILITY), [])

    def test_public_envelope_exposes_the_full_condition_list_for_audit(self) -> None:
        """데이터가 0행이어도 **서빙 중인 필터**를 HTTP 응답만으로 확인할 수 있어야 한다.

        `public_scopes` 만으로는 `is_private`/`publish_summary`/`access_state` 조건이 로드됐는지
        판별할 수 없다 — 그래서 봉투가 조건 전체를 싣는다.
        """
        from agi.service import ResearchIngestionService

        with isolated_schema() as (conn, schema):
            svc = ResearchIngestionService(schema=schema)
            envelope = svc.documents_envelope(conn, visibility=PUBLIC_VISIBILITY)
            conditions = envelope["public_document_conditions"]
            self.assertIn("d.is_private = false", conditions)
            self.assertIn("s.publish_summary <> 'denied'", conditions)
            self.assertIn("d.access_state = 'ok'", conditions)
            self.assertIn("s.scope IN ('public_channel', 'blog', 'report')", conditions)
            self.assertNotIn("public_document_conditions", svc.documents_envelope(
                conn, visibility=INTERNAL_VISIBILITY
            ))

    def test_without_the_access_state_clause_tombstoned_rows_would_leak(self) -> None:
        """변이 검증 — 조건을 떼면 실제로 샌다(가짜 수정 방지).

        이 테스트가 없으면 `access_state = 'ok'` 가 하중을 받고 있는지 알 수 없다:
        데이터가 0행이거나 다른 조건이 우연히 막아도 '공개 0건' 으로 보이기 때문이다.
        """
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema, publish_summary="owner_approval")
            adapter.ingest_page(
                conn, source_id, _listing([1, 2], complete=True), _details({1: "살아있음", 2: "삭제됨"})
            )
            conn.commit()
            adapter.ingest_page(conn, source_id, _listing([1], complete=True), _details({1: "살아있음"}))
            conn.commit()

            alive = make_document_id(source_id, "1")
            deleted = make_document_id(source_id, "2")
            self.assertEqual(
                [row["document_id"] for row in repo.list_documents(visibility=PUBLIC_VISIBILITY, limit=500)],
                [alive],
            )
            self.assertIsNone(repo.get_document(deleted, visibility=PUBLIC_VISIBILITY))

            def _clauses_without_access_state(visibility, **_kwargs):
                if visibility == PUBLIC_VISIBILITY:
                    return [
                        "d.is_private = false",
                        public_scope_clause("s"),
                        "s.publish_summary <> 'denied'",
                    ]
                return []

            with mock.patch.object(
                repository, "document_visibility_clauses", _clauses_without_access_state
            ):
                leaked = [
                    row["document_id"]
                    for row in repo.list_documents(visibility=PUBLIC_VISIBILITY, limit=500)
                ]
                self.assertIn(
                    deleted,
                    leaked,
                    "조건을 떼도 0건이면 이 테스트는 검증력을 잃는다(데이터가 없다)",
                )
                self.assertIsNotNone(
                    repo.get_document(deleted, visibility=PUBLIC_VISIBILITY),
                    "조건을 뗐을 때의 상세 누출이 재현되지 않는다",
                )


class AdapterIsolationTests(unittest.TestCase):
    """계약 §3 동결 — 어댑터는 legacy 테이블에 쓰지 않는다(문장 기록기로 증명)."""

    def test_no_writes_to_legacy_tables(self) -> None:
        with isolated_schema() as (conn, schema):
            recorder = StatementRecorder(conn)
            adapter, repo, source_id = _setup(recorder, schema)
            adapter.ingest_page(recorder, source_id, _listing([1, 2], complete=True), _details({1: "A", 2: "B"}))
            recorder.commit()
            adapter.ingest_page(recorder, source_id, _listing([1], complete=True), _details({1: "A"}))
            recorder.commit()

            self.assertEqual(recorder.writes_to(LEGACY_TABLES), [], "legacy 테이블에 쓰기가 발생했다")
            self.assertEqual(
                recorder.writes_to(("telegram_channel_posts",)),
                [],
                "블로그 어댑터가 텔레그램 원문 테이블을 건드렸다",
            )
            # 실제로 문장을 실행했음을 함께 남긴다(빈 기록이 '검증력 없음'이 아님을 증명).
            self.assertGreater(len(recorder.statements), 10)
            self.assertTrue(
                any("research_documents" in statement for statement in recorder.statements)
            )


class ExternalReview20261013Tests(unittest.TestCase):
    """2026-10-13 외부 검토(Codex) R1~R4 — 합성 데이터로 재현된 결함과 그 수정의 회귀 테스트.

    상세: docs/AGI_REVIEW_20261013.md. 재현 스크립트:
    scripts/review/reproduce_agi_review_20261013.py(그 자체는 진단용, 수용 테스트가 아니다).
    """

    # ---------------------------------------------------------------- R1: 원문 보관 권한
    def test_metadata_only_source_never_stores_body_text(self) -> None:
        """'메타데이터만 저장' 인데도 본문이 저장되던 결함(R1) — 이제는 body_text=NULL."""
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema, store_original="metadata_only")
            result = adapter.ingest_page(
                conn, source_id, _listing([1], complete=True), _details({1: "본문 A"})
            )
            conn.commit()

            document_id = make_document_id(source_id, "1")
            revisions = repo.list_revisions(document_id)
            self.assertEqual(len(revisions), 1, "metadata_only 인데 revision 자체가 안 생겼다")
            self.assertIsNone(revisions[0]["body_text"], "metadata_only 인데 본문이 저장되었다")
            self.assertIsNone(revisions[0]["body_uri"])
            # 변경 감지는 여전히 동작한다(해시는 메모리의 본문으로 계산, 저장만 안 한다).
            self.assertEqual(result["stored_count"], 1)

            second = adapter.ingest_page(
                conn, source_id, _listing([1], complete=True), _details({1: "본문 B"})
            )
            conn.commit()
            self.assertEqual(second["stored_count"], 1, "본문이 바뀌었는데 변경 감지가 안 됐다")
            revisions = repo.list_revisions(document_id)
            self.assertEqual(len(revisions), 2)
            self.assertEqual(revisions[1]["change_kind"], "text_edit")
            self.assertIsNone(revisions[1]["body_text"])

    def test_no_store_original_creates_no_revision_at_all(self) -> None:
        """'no' 는 변경 이력조차 남기지 않는다 — 문서 존재 사실(제목·발행시각)만 남는다."""
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema, store_original="no")
            result = adapter.ingest_page(
                conn, source_id, _listing([1], complete=True), _details({1: "본문"})
            )
            conn.commit()

            document_id = make_document_id(source_id, "1")
            self.assertEqual(repo.list_revisions(document_id), [])
            self.assertEqual(result["stored_count"], 0)
            self.assertEqual(result["skipped_duplicate"], 0)
            self.assertEqual(result["skipped_no_store_policy"], 1)
            document = repo.get_document(document_id, visibility=INTERNAL_VISIBILITY)
            self.assertIsNotNone(document, "문서 존재 사실조차 남지 않았다")
            self.assertEqual(document["access_state"], "ok")

    def test_full_store_original_still_keeps_the_body(self) -> None:
        """양성 대조 — 'yes' 는 그대로 본문을 보관한다(회귀가 본문을 아예 끊지 않았는지)."""
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema, store_original="yes")
            adapter.ingest_page(conn, source_id, _listing([1], complete=True), _details({1: "본문"}))
            conn.commit()
            document_id = make_document_id(source_id, "1")
            self.assertEqual(repo.list_revisions(document_id)[0]["body_text"], "본문")

    def test_repository_refuses_to_bypass_the_store_original_policy(self) -> None:
        """공통 저장 계층(ResearchRepository)도 독립적으로 우회를 막는다(방어적 2중 검사)."""
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema, store_original="metadata_only")
            document_id, _ = repo.store_document(
                source_id=source_id,
                external_id="1",
                canonical_uri=None,
                title=None,
                language=None,
                published_at=None,
                published_at_source="unknown",
                first_seen_at=now_utc(),
            )
            conn.commit()
            with self.assertRaises(ValueError):
                repo.store_revision(
                    document_id=document_id,
                    revision_hash="deadbeef",
                    text_digest="deadbeef",
                    change_kind="new",
                    collected_at=now_utc(),
                    body_text="어댑터를 건너뛰고 직접 넣은 본문",
                )

    def test_repository_refuses_any_revision_row_for_a_no_store_source(self) -> None:
        """2026-10-13 2차 외부 검토 P1 — 'no' 는 body_text=None 으로 불러도 여전히 거부된다.

        이전엔 body_text 가 있을 때만 정책을 조회해서, 어댑터의
        `should_create_revision_for_policy` 검사를 거치지 않고 저장소를 직접 불러
        body_text=None 을 넘기면 'no' 소스에도 revision 행이 생겼다(최소 감사 정보만
        허용해야 하는데 변경 이력까지 남은 것).
        """
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema, store_original="no")
            document_id, _ = repo.store_document(
                source_id=source_id,
                external_id="1",
                canonical_uri=None,
                title=None,
                language=None,
                published_at=None,
                published_at_source="unknown",
                first_seen_at=now_utc(),
            )
            conn.commit()
            with self.assertRaises(ValueError):
                repo.store_revision(
                    document_id=document_id,
                    revision_hash="deadbeef",
                    text_digest="deadbeef",
                    change_kind="new",
                    collected_at=now_utc(),
                    body_text=None,
                )
            self.assertEqual(repo.list_revisions(document_id), [], "거부됐는데 revision 행이 남았다")

    # ---------------------------------------------------------------- R2: A→B→A 포인터
    def test_revert_to_a_previous_body_updates_the_current_pointer(self) -> None:
        """A→B→A 재수집 후 현재 포인터는 A 를 가리켜야 한다(이전엔 B 에 머물렀다)."""
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema)
            page = _listing([1], complete=True)
            document_id = make_document_id(source_id, "1")

            adapter.ingest_page(conn, source_id, page, _details({1: "A"}))
            conn.commit()
            revision_a = repo.current_revision(document_id)

            adapter.ingest_page(conn, source_id, page, _details({1: "B"}))
            conn.commit()
            self.assertEqual(repo.current_revision(document_id)["body_text"], "B")

            result = adapter.ingest_page(conn, source_id, page, _details({1: "A"}))
            conn.commit()

            current = repo.current_revision(document_id)
            self.assertEqual(current["body_text"], "A", "되돌아온 본문인데 포인터가 그대로다")
            self.assertEqual(current["revision_hash"], revision_a["revision_hash"])
            document = repo.get_document(document_id, visibility=INTERNAL_VISIBILITY)
            self.assertEqual(document["current_revision_no"], revision_a["revision_no"])
            # 콘텐츠 중복 제거 자체는 그대로다 — revision 행이 3개로 늘지 않는다.
            self.assertEqual(len(repo.list_revisions(document_id)), 2)
            self.assertEqual(result["skipped_duplicate"], 1, "중복 제거 집계가 사라졌다")

    def test_reverting_to_a_previous_revision_records_the_new_observation(self) -> None:
        """2026-10-13 2차 외부 검토 P2 — A→B→A 복귀는 revision 1(A) 의 '마지막 관측'도 갱신한다.

        `collected_at`(최초 관측)은 바뀌지 않지만, `last_observed_at`/`last_observed_run_id`
        는 세 번째 실행의 시각·run 을 가리켜야 한다 — 이전엔 아무 데도 남지 않았다.
        """
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema)
            page = _listing([1], complete=True)
            document_id = make_document_id(source_id, "1")

            first_result = adapter.ingest_page(conn, source_id, page, _details({1: "A"}))
            conn.commit()
            revision_a_before = repo.current_revision(document_id)
            self.assertEqual(revision_a_before["last_observed_run_id"], first_result["run_id"])

            adapter.ingest_page(conn, source_id, page, _details({1: "B"}))
            conn.commit()
            third_result = adapter.ingest_page(conn, source_id, page, _details({1: "A"}))
            conn.commit()

            revision_a_after = repo.current_revision(document_id)
            self.assertEqual(revision_a_after["revision_no"], revision_a_before["revision_no"])
            self.assertEqual(revision_a_after["collected_at"], revision_a_before["collected_at"],
                              "최초 관측 시각이 바뀌었다 — PIT 근거가 손상됐다")
            self.assertNotEqual(third_result["run_id"], first_result["run_id"])
            self.assertEqual(revision_a_after["last_observed_run_id"], third_result["run_id"],
                              "다시 관측된 run 이 남지 않았다")
            self.assertGreaterEqual(revision_a_after["last_observed_at"], revision_a_before["last_observed_at"])

    # ---------------------------------------------------------------- R3: 상세 응답의 비공개
    def test_detail_marked_private_restricts_even_if_the_listing_said_public(self) -> None:
        """목록은 공개라고 했어도 상세(fetch) 응답 자체가 비공개면 그 사실을 신뢰한다."""
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(
                conn, schema, publish_summary="allowed"
            )
            page = _listing([1], complete=True)  # 목록은 비공개 표시 없음
            document_id = make_document_id(source_id, "1")

            adapter.ingest_page(conn, source_id, page, _details({1: "공개 본문"}))
            conn.commit()
            self.assertIsNotNone(repo.get_document(document_id, visibility=PUBLIC_VISIBILITY))

            result = adapter.ingest_page(
                conn,
                source_id,
                page,
                fixture_fetch({"1": {"body_text": "비공개로 전환된 본문", "is_private": True}}),
            )
            conn.commit()

            document = repo.get_document(document_id, visibility=INTERNAL_VISIBILITY)
            self.assertEqual(document["access_state"], "restricted")
            self.assertEqual(document["tombstone_reason"], "detail_marked_private")
            self.assertIsNone(
                repo.get_document(document_id, visibility=PUBLIC_VISIBILITY),
                "비공개로 전환됐는데 공개 조회에 남아 있다",
            )
            # 비공개로 전환된 본문은 revision 으로 저장되지 않는다(R1 의 '본문을 가져오지
            # 않는다' 원칙과 동일 — 여기선 fetch 가 끝난 뒤에야 알았을 뿐이다).
            self.assertEqual(len(repo.list_revisions(document_id)), 1)
            self.assertEqual(repo.list_revisions(document_id)[0]["body_text"], "공개 본문")
            self.assertEqual(result["skipped_restricted"], 1)

    # ---------------------------------------------------------------- R4: 마지막 페이지 오판
    def test_a_lone_last_page_call_never_sweeps_earlier_pages(self) -> None:
        """마지막 페이지 하나만 단독으로 들어오면(이전 페이지를 모르는 채) 스윕하지 않는다."""
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema)
            first_page = _listing([1], complete=False, truncated=True, limitation="more pages")
            adapter.ingest_page(conn, source_id, first_page, _details({1: "이전 페이지 글"}))
            conn.commit()
            older_id = make_document_id(source_id, "1")
            self.assertEqual(
                repo.get_document(older_id, visibility=INTERNAL_VISIBILITY)["access_state"], "ok"
            )

            last_page = ListingPage(
                entries=(ListingEntry(external_id="2", url="https://demo.blog/2"),),
                complete=True,
                page_index=2,  # 이 crawl walk 의 두 번째(=마지막) 페이지
            )
            result = adapter.ingest_page(conn, source_id, last_page, _details({2: "마지막 페이지 글"}))
            conn.commit()

            self.assertFalse(result["tombstone_sweep_performed"], "page_index>1 인데 스윕했다")
            self.assertEqual(
                repo.get_document(older_id, visibility=INTERNAL_VISIBILITY)["access_state"],
                "ok",
                "이전 페이지의 살아 있는 글이 잘못 삭제되었다",
            )
            self.assertTrue(
                any("listing_page_index_gt_1" in note for note in result["limitations"]),
                "스윕을 보류한 이유가 결과에 없다",
            )

    def test_ingest_full_listing_sweeps_using_the_union_of_all_pages(self) -> None:
        """여러 페이지를 모아 전달하면(연속·마지막 페이지 확인) 합친 집합으로 스윕한다."""
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema)
            adapter.ingest_page(
                conn, source_id, _listing([1, 2, 3], complete=True), _details({1: "A", 2: "B", 3: "C"})
            )
            conn.commit()
            removed_id = make_document_id(source_id, "2")

            page_1 = ListingPage(
                entries=(ListingEntry(external_id="1", url="https://demo.blog/1"),),
                truncated=True,
                limitation="more pages",
                page_index=1,
                page_continues=True,
            )
            page_2 = ListingPage(
                entries=(ListingEntry(external_id="3", url="https://demo.blog/3"),),
                complete=True,
                page_index=2,
            )
            result = adapter.ingest_full_listing(
                conn, source_id, [page_2, page_1], _details({1: "A", 3: "C"})
            )
            conn.commit()

            self.assertTrue(result["tombstone_sweep_performed"])
            self.assertEqual(result["tombstones_from_sweep"], 1)
            document = repo.get_document(removed_id, visibility=INTERNAL_VISIBILITY)
            self.assertEqual(document["access_state"], "deleted")
            for external_id in ("1", "3"):
                self.assertEqual(
                    repo.get_document(
                        make_document_id(source_id, external_id), visibility=INTERNAL_VISIBILITY
                    )["access_state"],
                    "ok",
                )

    def test_ingest_full_listing_refuses_to_sweep_when_pages_are_not_contiguous(self) -> None:
        """page_index 에 빈 자리가 있으면(중간 페이지 누락) 완전한 목록으로 보지 않는다."""
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema)
            adapter.ingest_page(
                conn, source_id, _listing([1, 2], complete=True), _details({1: "A", 2: "B"})
            )
            conn.commit()

            page_1 = ListingPage(
                entries=(ListingEntry(external_id="1", url="https://demo.blog/1"),),
                truncated=True,
                limitation="more pages",
                page_index=1,
                page_continues=True,
            )
            page_3 = ListingPage(  # 2번 페이지가 빠졌다
                entries=(ListingEntry(external_id="3", url="https://demo.blog/3"),),
                complete=True,
                page_index=3,
            )
            result = adapter.ingest_full_listing(
                conn, source_id, [page_1, page_3], _details({1: "A", 3: "C"})
            )
            conn.commit()

            self.assertFalse(result["tombstone_sweep_performed"])
            self.assertTrue(
                any("listing_pages_not_contiguous" in note for note in result["limitations"])
            )
            self.assertEqual(
                repo.get_document(
                    make_document_id(source_id, "2"), visibility=INTERNAL_VISIBILITY
                )["access_state"],
                "ok",
                "연속성이 깨졌는데도 스윕되어 2 번 글이 삭제되었다",
            )

    def test_ingest_full_listing_refuses_a_middle_page_truncated_by_entry_limit(self) -> None:
        """2026-10-13 2차 외부 검토 P1 — 항목 상한으로 잘린 중간 페이지는 '다음 페이지가
        있다고 스스로 말한' 페이지가 아니다(이 페이지 자신의 항목도 빠졌을 수 있다).
        `truncated=True` 만 보고 승인하면, 잘려 나간 그 항목은 어떤 페이지에도 나타나지
        않아 '완전한 목록'이 거짓이 되고, 그 항목이 실제로 삭제된 글이면 스윕이 놓친다.
        """
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema)
            adapter.ingest_page(
                conn, source_id, _listing([1, 2, 3], complete=True), _details({1: "A", 2: "B", 3: "C"})
            )
            conn.commit()

            page_1 = ListingPage(  # 항목 상한으로 잘림(hasNextPage 마커 아님) — page_continues 없음
                entries=(ListingEntry(external_id="1", url="https://demo.blog/1"),),
                truncated=True,
                limitation="entry_limit_reached",
                page_index=1,
            )
            page_2 = ListingPage(
                entries=(ListingEntry(external_id="3", url="https://demo.blog/3"),),
                complete=True,
                page_index=2,
            )
            result = adapter.ingest_full_listing(
                conn, source_id, [page_1, page_2], _details({1: "A", 3: "C"})
            )
            conn.commit()

            self.assertFalse(
                result["tombstone_sweep_performed"], "항목 상한으로 잘린 페이지를 전체 목록으로 승인했다"
            )
            self.assertTrue(
                any("listing_middle_page_not_marked_truncated" in note for note in result["limitations"])
            )
            self.assertEqual(
                repo.get_document(
                    make_document_id(source_id, "2"), visibility=INTERNAL_VISIBILITY
                )["access_state"],
                "ok",
                "잘린 중간 페이지를 완전한 목록으로 받아들여 2 번 글이 삭제되었다",
            )


class SelectiveIngestionTests(unittest.TestCase):
    """2026-10-13 사용자 지시 — 전체 목록 대신 **특정 글만** 수집."""

    def test_ingest_known_posts_never_claims_completeness_or_sweeps(self) -> None:
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema)
            # 기존에 글 2개를 저장해 둔다(전체 목록 스캔을 거쳤다고 가정).
            adapter.ingest_page(
                conn, source_id, _listing([1, 2], complete=True), _details({1: "A", 2: "B"})
            )
            conn.commit()

            entries = (ListingEntry(external_id="3", url="https://demo.blog/3"),)
            result = adapter.ingest_known_posts(conn, source_id, entries, _details({3: "C"}))
            conn.commit()

            self.assertEqual(result["coverage"], "denominator_unknown")
            self.assertFalse(result["tombstone_sweep_performed"], "특정 글만 넘겼는데 스윕을 했다")
            self.assertEqual(result["stored_count"], 1)
            doc3 = repo.get_document(make_document_id(source_id, "3"), visibility=INTERNAL_VISIBILITY)
            self.assertEqual(doc3["access_state"], "ok")
            # 넘기지 않은 기존 글은 '목록에 없다'는 이유로 삭제되지 않는다.
            for external_id in ("1", "2"):
                self.assertEqual(
                    repo.get_document(
                        make_document_id(source_id, external_id), visibility=INTERNAL_VISIBILITY
                    )["access_state"],
                    "ok",
                )

    def test_ingest_known_posts_ignores_a_caller_supplied_complete_flag(self) -> None:
        """손으로 고른 목록을 '전체'라고 우겨도 내부적으로 강제 denominator_unknown."""
        with isolated_schema() as (conn, schema):
            adapter, repo, source_id = _setup(conn, schema)
            adapter.ingest_page(conn, source_id, _listing([1], complete=True), _details({1: "A"}))
            conn.commit()
            entries = (ListingEntry(external_id="2", url="https://demo.blog/2"),)
            result = adapter.ingest_known_posts(conn, source_id, entries, _details({2: "B"}))
            conn.commit()
            self.assertFalse(result["tombstone_sweep_performed"])
            self.assertEqual(
                repo.get_document(make_document_id(source_id, "1"), visibility=INTERNAL_VISIBILITY)[
                    "access_state"
                ],
                "ok",
                "손으로 고른 글만 넘겼는데 1 번 글이 삭제되었다",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
