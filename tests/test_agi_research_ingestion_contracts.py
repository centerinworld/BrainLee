"""AGI 원문 보관 계약 테스트 — 멱등·수정·중단재개·시간·권한.

계약 정본: `docs/AGI_01_SOURCE_TIME_AUTH_CONTRACT_20261011.md`
  * §5-1 `revision_hash` 정규 알고리즘 + 첨부 정렬 규칙  → 수용기준 T14
  * §5-2 실행 원장 ↔ 엔티티 구분                        → 수용기준 T1
  * §4   시간 4종·M2 포맷 2종                            → 수용기준 T4
  * §6   완전성(분모 미확인) 표기                        → 수용기준 T8

실행:
    ./venv/bin/python -m pytest tests/test_agi_research_ingestion_contracts.py -v
"""

from __future__ import annotations

import random
import re
import unittest
from unittest import mock

from agi import revision, repository
from agi.repository import (
    INTERNAL_VISIBILITY,
    NON_PUBLIC_SCOPES,
    PUBLIC_SCOPES,
    PUBLIC_VISIBILITY,
    ResearchRepository,
    document_visibility_clauses,
    make_document_id,
    make_source_id,
    public_scope_clause,
    source_visibility_clauses,
)
from agi.service import ResearchIngestionService, classify_telegram_scope
from agi.timeutil import parse_legacy_kst
from tests.agi_test_support import StatementRecorder, isolated_schema

LEGACY_TABLES = ("telegram_channel_posts", "telegram_channels")

ALLOWLIST_SQL = "s.scope IN ('public_channel', 'blog', 'report')"
BARE_ALLOWLIST_SQL = "scope IN ('public_channel', 'blog', 'report')"


# --------------------------------------------------------------------------- 순수 로직
class RevisionHashDeterminismTests(unittest.TestCase):
    """T14 — 첨부 순서·중복·경로·플레이스홀더에 대한 해시 결정성."""

    def test_attachment_order_does_not_change_the_hash(self) -> None:
        attachments = [
            ("docs/b.pdf", b"BBB"),
            ("docs/a.pdf", b"AAA"),
            ("docs/c.pdf", b"CCC"),
        ]
        first = revision.compute_revision_hash("본문", attachments)[0]
        for seed in range(5):
            shuffled = list(attachments)
            random.Random(seed).shuffle(shuffled)
            self.assertEqual(revision.compute_revision_hash("본문", shuffled)[0], first)
        self.assertEqual(revision.compute_revision_hash("본문", list(reversed(attachments)))[0], first)

    def test_duplicate_attachment_is_collapsed(self) -> None:
        once = revision.compute_revision_hash("t", [("a.txt", b"x")])[0]
        twice = revision.compute_revision_hash("t", [("a.txt", b"x"), ("a.txt", b"x")])[0]
        self.assertEqual(once, twice)

    def test_same_name_different_bytes_is_a_different_revision(self) -> None:
        left = revision.compute_revision_hash("t", [("a.txt", b"x")])[0]
        right = revision.compute_revision_hash("t", [("a.txt", b"y")])[0]
        self.assertNotEqual(left, right)

    def test_rename_changes_the_hash_and_is_classified(self) -> None:
        before = revision.compute_revision_hash("t", [("old/name.txt", b"same-bytes")])
        after = revision.compute_revision_hash("t", [("new/name.txt", b"same-bytes")])
        self.assertNotEqual(before[0], after[0])
        self.assertEqual(
            revision.classify_change(("d", before[2]), ("d", after[2])), "attachment_renamed"
        )

    def test_pathless_attachment_uses_nopath_index(self) -> None:
        _, _, entries = revision.compute_revision_hash("t", [(None, b"x")])
        self.assertEqual(entries[0].split(revision.ENTRY_SEPARATOR)[0], "__nopath__/0001")
        _, _, second = revision.compute_revision_hash("t", [(None, b"x"), ("", b"y")])
        self.assertEqual(
            sorted(entry.split(revision.ENTRY_SEPARATOR)[0] for entry in second),
            ["__nopath__/0001", "__nopath__/0002"],
        )

    def test_no_attachment_uses_empty_join(self) -> None:
        expected = revision._sha256_hex(
            ("v1\n" + revision.text_digest("t") + "\n").encode("utf-8")
        )
        self.assertEqual(revision.compute_revision_hash("t", [])[0], expected)
        self.assertEqual(revision.compute_revision_hash("t", [])[2], [])

    def test_algorithm_prefix_is_part_of_the_hash(self) -> None:
        digest = revision.text_digest("t")
        v1 = revision._sha256_hex(("v1\n" + digest + "\n").encode("utf-8"))
        v2 = revision._sha256_hex(("v2\n" + digest + "\n").encode("utf-8"))
        self.assertNotEqual(v1, v2)
        self.assertEqual(revision.ALGORITHM_VERSION, "v1")

    def test_text_normalization(self) -> None:
        self.assertEqual(revision.normalize_text("  a   b  \r\n c \r\n"), "a b\nc")
        self.assertEqual(revision.text_digest("a  b"), revision.text_digest("a b"))
        self.assertEqual(revision.normalize_text(None), "")


class ChangeKindTests(unittest.TestCase):
    def test_new_document(self) -> None:
        self.assertEqual(revision.classify_change(None, ("d", [])), "new")

    def test_text_edit(self) -> None:
        before = ("d1", [])
        self.assertEqual(revision.classify_change(before, ("d2", [])), "text_edit")

    def test_attachment_added_and_removed(self) -> None:
        before = ("d", [revision.attachment_entry("a.txt", b"a")])
        after_add = ("d", before[1] + [revision.attachment_entry("b.txt", b"b")])
        self.assertEqual(revision.classify_change(before, after_add), "attachment_added")
        self.assertEqual(revision.classify_change(after_add, before), "attachment_removed")

    def test_mixed(self) -> None:
        before = ("d1", [revision.attachment_entry("a.txt", b"a")])
        after = ("d2", [revision.attachment_entry("a.txt", b"a"), revision.attachment_entry("b.txt", b"b")])
        self.assertEqual(revision.classify_change(before, after), "mixed")

    def test_results_are_within_the_contract_enum(self) -> None:
        cases = [
            (None, ("d", [])),
            (("d1", []), ("d2", [])),
            (("d", []), ("d", [revision.attachment_entry("a", b"1")])),
        ]
        for before, after in cases:
            self.assertIn(revision.classify_change(before, after), revision.CHANGE_KINDS)


class LegacyTimeFormatTests(unittest.TestCase):
    """M2: 같은 테이블에 공백 포맷과 ISO T 포맷이 섞여 있다(둘 다 오프셋 없음 = KST)."""

    def test_both_formats_parse_to_the_same_instant(self) -> None:
        space = parse_legacy_kst("2026-10-10 03:41:30")
        iso = parse_legacy_kst("2026-10-10T03:41:30")
        self.assertIsNotNone(space)
        self.assertEqual(space, iso)
        self.assertEqual(space.isoformat(), "2026-10-09T18:41:30+00:00")

    def test_lexicographic_text_order_differs_from_chronological_order(self) -> None:
        """계약 §4-3 의 근거 — TEXT 사전식 정렬은 실제 시간 순서와 다르다."""
        space = "2026-10-10 23:00:00"
        iso = "2026-10-10T01:00:00"
        self.assertLess(space, iso)  # 사전식: ' '(0x20) < 'T'(0x54) → 뒤 시간이 앞으로 온다
        self.assertGreater(
            parse_legacy_kst(space), parse_legacy_kst(iso)  # 실제로는 23:00 이 01:00 보다 늦다
        )

    def test_unparseable_input_returns_none(self) -> None:
        self.assertIsNone(parse_legacy_kst(""))
        self.assertIsNone(parse_legacy_kst(None))
        self.assertIsNone(parse_legacy_kst("2026/10/10 03:41:30"))

    def test_explicit_offset_is_respected(self) -> None:
        parsed = parse_legacy_kst("2026-10-10T03:41:30+00:00")
        self.assertEqual(parsed.isoformat(), "2026-10-10T03:41:30+00:00")


class ScopeClassificationTests(unittest.TestCase):
    def test_supergroup_id_is_public(self) -> None:
        self.assertEqual(classify_telegram_scope("체리형부 26.1Q", "-1003740807689"), "public_channel")

    def test_at_username_is_public(self) -> None:
        self.assertEqual(classify_telegram_scope("Valuefs1", "@Valuefs1"), "public_channel")

    def test_bare_username_is_public(self) -> None:
        for name in ("sunstudy1004", "yigreport", "DOC_POOL", "jake8lee", "petrojimmy"):
            self.assertEqual(classify_telegram_scope(name, None), "public_channel", name)

    def test_positive_integer_is_a_private_dialog(self) -> None:
        self.assertEqual(classify_telegram_scope("123456789", None), "private_chat")
        self.assertEqual(classify_telegram_scope("someone", "555"), "private_chat")

    def test_unprovable_source_is_not_registered(self) -> None:
        """공개임을 증명할 수 없으면 `other` — 조용히 공개로 승격하지 않는다(fail-closed)."""
        self.assertEqual(classify_telegram_scope("선진짱 주식공부방", None), "other")
        self.assertEqual(classify_telegram_scope("@채널명", None), "other")


# --------------------------------------------------------------------------- DB 계약
def _post(message_id: int, text: str, *, msg_date="2026-01-01 00:00:00", collected="2026-01-01T00:00:05",
          photo: str = "", file_name: str = "") -> dict:
    return {
        "message_id": message_id,
        "text": text,
        "msg_date": msg_date,
        "collected_at": collected,
        "photo_path": photo,
        "file_name": file_name,
    }


def _busiest_channel(conn) -> str:
    row = conn.execute(
        "SELECT channel_id FROM telegram_channel_posts "
        "GROUP BY channel_id ORDER BY COUNT(*) DESC, channel_id LIMIT 1"
    ).fetchone()
    return str(row["channel_id"])


def _busiest_channel_posts(conn) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM telegram_channel_posts "
        "GROUP BY channel_id ORDER BY COUNT(*) DESC, channel_id LIMIT 1"
    ).fetchone()
    return int(row["c"]) if row is not None else 0


class IngestionContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from agi.db import connect_primary

        cls.probe = connect_primary(timeout=30)
        try:
            cls.busiest = _busiest_channel(cls.probe)
            cls.busiest_posts = _busiest_channel_posts(cls.probe)
        finally:
            cls.probe.close()

    def setUp(self) -> None:
        # 시드는 **레거시 데이터**를 입력으로 쓴다(합성 입력이 아니다). 가장 큰 채널이
        # 36건 미만이면 재개·멱등 검증 표본이 부족하므로 명시적으로 skip 한다
        # (조용히 약한 검증으로 통과시키지 않는다).
        if self.busiest_posts < 40:
            self.skipTest(
                f"가장 큰 텔레그램 채널의 글이 {self.busiest_posts}건뿐이라 표본 부족"
            )

    def _seed(self, conn, schema):
        svc = ResearchIngestionService(schema=schema)
        seed = svc.seed_sources_from_telegram(conn)
        conn.commit()
        by_channel = {item["channel_id"]: item["source_id"] for item in seed["registered"]}
        return svc, seed, by_channel

    # ---------------------------------------------------------------- T1 멱등
    def test_three_reruns_add_no_entities_but_append_run_rows(self) -> None:
        with isolated_schema() as (conn, schema):
            svc, seed, by_channel = self._seed(conn, schema)
            self.assertIn(self.busiest, by_channel, "가장 큰 채널이 공개로 분류되지 않았다")
            source_id = by_channel[self.busiest]

            runs = []
            fingerprints = []
            for _ in range(3):
                result = svc.ingest_source(conn, source_id, limit=15, run_kind="seed")
                conn.commit()
                runs.append(result)
                fingerprints.append(ResearchRepository(conn, schema=schema).snapshot_fingerprint())

            self.assertEqual(runs[0]["stored_count"], 15)
            self.assertEqual(runs[0]["skipped_duplicate"], 0)
            for run in runs[1:]:
                self.assertEqual(run["stored_count"], 0)
                self.assertEqual(run["skipped_duplicate"], runs[0]["listed_count"])

            # 엔티티 중복 0 — 문서/revision 이 실행 횟수만큼 늘지 않는다.
            self.assertEqual(fingerprints[0]["documents"], 15)
            self.assertEqual(fingerprints[0]["revisions"], 15)
            self.assertEqual(fingerprints[2]["documents"], 15)
            self.assertEqual(fingerprints[2]["revisions"], 15)
            # 실행 원장은 매 실행 새 행이다(§5-2).
            self.assertEqual(fingerprints[2]["runs"], 3)
            # 3행째 skipped.duplicate == 2행째(계약 T1).
            self.assertEqual(runs[2]["skipped_duplicate"], runs[1]["skipped_duplicate"])

            # 소스 카운터는 **누적**이다(실행별 값은 run 행에 있다 — 두 계층을 섞지 않는다).
            source = ResearchRepository(conn, schema=schema).get_source(source_id)
            self.assertEqual(source["stored_count"], 15)
            self.assertEqual(source["skipped_duplicate"], 30)
            self.assertEqual(source["listed_count"], 45)
            self.assertEqual(source["page_cursor"], runs[-1]["cursor_end"])

            self.assertEqual(len(set(result["cursor_end"] for result in runs)), 1)
            self.assertEqual(len(set(result["source_id"] for result in runs)), 1)

    def test_prior_run_rows_are_not_mutated_by_later_runs(self) -> None:
        with isolated_schema() as (conn, schema):
            svc, _, by_channel = self._seed(conn, schema)
            repo = ResearchRepository(conn, schema=schema)
            source_id = by_channel[self.busiest]

            first = svc.ingest_source(conn, source_id, limit=5)
            conn.commit()
            snapshot = repo.get_run(first["run_id"])
            self.assertIsNotNone(snapshot)

            svc.ingest_source(conn, source_id, limit=5)
            svc.ingest_source(conn, source_id, limit=5)
            conn.commit()

            after = repo.get_run(first["run_id"])
            self.assertEqual(snapshot, after, "이전 실행 행이 이후 실행에 의해 수정되었다")
            self.assertEqual(len(repo.list_runs(source_id=source_id)), 3)

    # ------------------------------------------------- 선택 수집(2026-10-13 사용자 지시)
    def test_ingest_specific_messages_only_stores_the_requested_ids(self) -> None:
        """전체 백필이 아니라 **지정한 message_id만** 저장하고, 커서는 건드리지 않는다."""
        with isolated_schema() as (conn, schema):
            svc, seed, by_channel = self._seed(conn, schema)
            source_id = by_channel[self.busiest]
            repo = ResearchRepository(conn, schema=schema)
            # 레거시 테이블은 public 에 있다 — 같은 연결(conn)로 그냥 읽는다(별도 연결 불필요).
            real_ids = [
                int(row["message_id"])
                for row in conn.execute(
                    "SELECT message_id FROM telegram_channel_posts WHERE channel_id = %s "
                    "ORDER BY message_id ASC LIMIT 3",
                    (self.busiest,),
                ).fetchall()
            ]
            self.assertEqual(len(real_ids), 3, "표본 채널에 message_id 3개가 없다")
            picked = [real_ids[0], real_ids[2]]  # 중간 1개를 일부러 건너뛴다

            result = svc.ingest_specific_messages(conn, source_id, picked)
            conn.commit()

            self.assertEqual(result["requested_count"], 2)
            self.assertEqual(result["found_count"], 2)
            self.assertEqual(result["stored_count"], 2)
            self.assertEqual(result["missing_ids"], [])

            # 요청한 2건만 저장되고, 건너뛴 가운데 글은 저장되지 않았다.
            for mid in picked:
                doc = repo.get_document(make_document_id(source_id, str(mid)), visibility=INTERNAL_VISIBILITY)
                self.assertIsNotNone(doc, f"{mid} 가 저장되지 않았다")
            skipped_doc = repo.get_document(
                make_document_id(source_id, str(real_ids[1])), visibility=INTERNAL_VISIBILITY
            )
            self.assertIsNone(skipped_doc, "건너뛴 message_id 가 저장되었다 — 전체 백필처럼 동작했다")

            # 커서는 건드리지 않는다 — 이후 backfill 의 시작점이 선택 수집으로 어긋나면 안 된다.
            source_after = repo.get_source(source_id)
            self.assertIsNone(source_after["page_cursor"])

    def test_ingest_specific_messages_reports_ids_not_found(self) -> None:
        with isolated_schema() as (conn, schema):
            svc, seed, by_channel = self._seed(conn, schema)
            source_id = by_channel[self.busiest]
            result = svc.ingest_specific_messages(conn, source_id, [-999999])
            conn.commit()
            self.assertEqual(result["found_count"], 0)
            self.assertEqual(result["stored_count"], 0)
            self.assertEqual(result["missing_ids"], [-999999])

    # ---------------------------------------------------------------- T2 수정
    def test_text_edit_adds_one_revision_and_keeps_the_old_one(self) -> None:
        with isolated_schema() as (conn, schema):
            svc = ResearchIngestionService(schema=schema)
            repo = ResearchRepository(conn, schema=schema)
            source_id = repo.ensure_source(
                source_kind="telegram_channel",
                source_owner="owner",
                source_native_id="synthetic-channel",
            )
            conn.commit()
            run_id = repo.begin_run(source_id=source_id, run_kind="seed")
            conn.commit()

            self.assertEqual(
                svc._store_post(repo, source_id, "synthetic-channel", False, _post(1, "원문 A"), run_id, "metadata_only"),
                "stored",
            )
            conn.commit()
            document_id = make_document_id(source_id, "1")
            first = repo.list_revisions(document_id)
            self.assertEqual(len(first), 1)
            self.assertEqual(first[0]["change_kind"], "new")
            self.assertEqual(
                repo.get_document(document_id, visibility=INTERNAL_VISIBILITY)["current_revision_no"], 1
            )

            # 같은 본문 재수집 → revision 추가 0
            self.assertEqual(
                svc._store_post(repo, source_id, "synthetic-channel", False, _post(1, "원문 A"), run_id, "metadata_only"),
                "duplicate",
            )
            conn.commit()
            self.assertEqual(len(repo.list_revisions(document_id)), 1)

            # 본문 수정 → revision 1개 추가, 기존 revision 불변
            self.assertEqual(
                svc._store_post(repo, source_id, "synthetic-channel", False, _post(1, "원문 B"), run_id, "metadata_only"),
                "stored",
            )
            conn.commit()
            revisions = repo.list_revisions(document_id)
            self.assertEqual(len(revisions), 2)
            self.assertEqual(revisions[1]["change_kind"], "text_edit")
            self.assertEqual(revisions[1]["revision_no"], 2)
            self.assertEqual(revisions[0]["revision_hash"], first[0]["revision_hash"])
            self.assertEqual(revisions[0]["text_digest"], first[0]["text_digest"])
            self.assertEqual(
                repo.get_document(document_id, visibility=INTERNAL_VISIBILITY)["current_revision_no"], 2
            )

    def test_store_post_distinguishes_policy_skip_from_content_duplicate(self) -> None:
        """2026-10-14 외부 검토 반영: `store_original='no'` 스킵은 `"duplicate"`가 아니다.

        이전엔 `_store_post`가 bool만 돌려줘서 호출부(`ingest_source`)가 두 원인을
        `skipped_duplicate` 하나에 합산했다 — 정책 스킵이 중복률을 부풀렸다. 이제 세
        상태(`"stored"`/`"duplicate"`/`"policy_skipped"`)가 섞이지 않아야 한다.
        """
        with isolated_schema() as (conn, schema):
            svc = ResearchIngestionService(schema=schema)
            repo = ResearchRepository(conn, schema=schema)
            source_id = repo.ensure_source(
                source_kind="telegram_channel",
                source_owner="owner",
                source_native_id="no-store-channel",
            )
            conn.execute(
                f"UPDATE {repo._t('research_sources')} SET store_original = 'no' WHERE source_id = %s",
                (source_id,),
            )
            conn.commit()
            run_id = repo.begin_run(source_id=source_id, run_kind="seed")
            conn.commit()

            # 정책상 'no' — 같은 글을 두 번 넣어도 매번 policy_skipped, duplicate 로 세지 않는다.
            self.assertEqual(
                svc._store_post(repo, source_id, "no-store-channel", False, _post(1, "원문 A"), run_id, "no"),
                "policy_skipped",
            )
            self.assertEqual(
                svc._store_post(repo, source_id, "no-store-channel", False, _post(1, "원문 A"), run_id, "no"),
                "policy_skipped",
            )
            conn.commit()
            document_id = make_document_id(source_id, "1")
            self.assertEqual(repo.list_revisions(document_id), [], "정책상 revision이 전혀 없어야 한다")

    def test_ingest_source_reports_policy_skips_separately_from_duplicates(self) -> None:
        """`ingest_source`가 `_store_post`의 세 상태를 각각 다른 카운터·DB 컬럼에 쌓는지 확인한다."""
        with isolated_schema() as (conn, schema):
            svc, seed, by_channel = self._seed(conn, schema)
            self.assertIn(self.busiest, by_channel, "가장 큰 채널이 공개로 분류되지 않았다")
            source_id = by_channel[self.busiest]
            repo = ResearchRepository(conn, schema=schema)
            conn.execute(
                f"UPDATE {repo._t('research_sources')} SET store_original = 'no' WHERE source_id = %s",
                (source_id,),
            )
            conn.commit()

            result = svc.ingest_source(conn, source_id, limit=15, run_kind="seed")
            conn.commit()

            # 'no' 정책이면 전부 policy_skipped다 — stored/duplicate 는 0.
            self.assertEqual(result["stored_count"], 0)
            self.assertEqual(result["skipped_duplicate"], 0)
            self.assertEqual(result["skipped_no_store_policy"], 15)

            source = repo.get_source(source_id)
            self.assertEqual(source["skipped_duplicate"], 0)
            self.assertEqual(source["skipped_no_store_policy"], 15)

    def test_tombstone_keeps_document_and_revisions(self) -> None:
        with isolated_schema() as (conn, schema):
            svc = ResearchIngestionService(schema=schema)
            repo = ResearchRepository(conn, schema=schema)
            source_id = repo.ensure_source(
                source_kind="telegram_channel",
                source_owner="owner",
                source_native_id="synthetic-channel",
            )
            conn.commit()
            run_id = repo.begin_run(source_id=source_id, run_kind="seed")
            conn.commit()
            svc._store_post(repo, source_id, "synthetic-channel", False, _post(1, "본문"), run_id, "metadata_only")
            conn.commit()
            document_id = make_document_id(source_id, "1")

            self.assertTrue(
                repo.mark_access_state(
                    document_id, access_state="restricted", reason="권한 없음"
                )
            )
            conn.commit()
            doc = repo.get_document(document_id, visibility=INTERNAL_VISIBILITY)
            self.assertEqual(doc["access_state"], "restricted")
            self.assertEqual(doc["tombstone_reason"], "권한 없음")
            self.assertIsNotNone(doc["tombstoned_at"])
            self.assertEqual(len(repo.list_revisions(document_id)), 1, "revision 이 삭제되었다")

            with self.assertRaises(ValueError):
                repo.mark_access_state(document_id, access_state="removed")

    def test_personal_documents_are_excluded_from_the_public_view(self) -> None:
        """§9-4: 개인 대화는 공개 뷰에서 빠지고 인증 뷰(internal)에서만 보인다.

        소스의 `publish_summary` 를 `allowed` 로 올려 **개인 대화 필터만** 따로 검증한다
        (기본값 `denied` 이면 공개 뷰가 두 규칙 때문에 동시에 비게 되어 원인이 흐려진다).
        """
        with isolated_schema() as (conn, schema):
            svc = ResearchIngestionService(schema=schema)
            repo = ResearchRepository(conn, schema=schema)
            source_id = repo.ensure_source(
                source_kind="telegram_channel",
                source_owner="owner",
                source_native_id="123456789",
                scope="private_chat",
            )
            conn.execute(
                f'UPDATE "{schema}".research_sources SET publish_summary = %s WHERE source_id = %s',
                ("allowed", source_id),
            )
            conn.commit()
            run_id = repo.begin_run(source_id=source_id, run_kind="seed")
            conn.commit()
            svc._store_post(repo, source_id, "123456789", True, _post(1, "개인 대화"), run_id, "metadata_only")
            conn.commit()

            self.assertEqual([], repo.list_documents(visibility=PUBLIC_VISIBILITY))
            self.assertEqual(
                len(repo.list_documents(visibility=INTERNAL_VISIBILITY)), 1
            )
            self.assertEqual(
                [s for s in repo.list_sources(visibility=PUBLIC_VISIBILITY)], []
            )
            with self.assertRaises(ValueError):
                repo.list_documents(visibility="everyone")

    def test_denied_sources_are_excluded_from_the_public_view(self) -> None:
        """`publish_summary='denied'`(기본값) 소스의 근거는 인증 뷰에만 있다."""
        with isolated_schema() as (conn, schema):
            svc = ResearchIngestionService(schema=schema)
            repo = ResearchRepository(conn, schema=schema)
            source_id = repo.ensure_source(
                source_kind="telegram_channel",
                source_owner="owner",
                source_native_id="public-channel",
            )
            conn.commit()
            run_id = repo.begin_run(source_id=source_id, run_kind="seed")
            conn.commit()
            svc._store_post(repo, source_id, "public-channel", False, _post(1, "본문"), run_id, "metadata_only")
            conn.commit()

            self.assertEqual([], repo.list_documents(visibility=PUBLIC_VISIBILITY))
            self.assertEqual(len(repo.list_documents(visibility=INTERNAL_VISIBILITY)), 1)

            conn.execute(
                f'UPDATE "{schema}".research_sources SET publish_summary = %s WHERE source_id = %s',
                ("owner_approval", source_id),
            )
            conn.commit()
            self.assertEqual(len(repo.list_documents(visibility=PUBLIC_VISIBILITY)), 1)

    # ---------------------------------------------------------------- T3 중단재개
    def test_interrupted_then_resumed_equals_one_shot(self) -> None:
        with isolated_schema() as (conn_a, schema_a), isolated_schema() as (conn_b, schema_b):
            svc_a, _, by_channel_a = self._seed(conn_a, schema_a)
            svc_b, _, by_channel_b = self._seed(conn_b, schema_b)
            source_a = by_channel_a[self.busiest]
            source_b = by_channel_b[self.busiest]
            self.assertEqual(source_a, source_b, "source_id 가 결정적이지 않다")

            resumed = svc_a.backfill(conn_a, source_a, batch_size=12, max_batches=3)
            conn_a.commit()
            one_shot = svc_b.ingest_source(conn_b, source_b, limit=36, run_kind="seed")
            conn_b.commit()

            repo_a = ResearchRepository(conn_a, schema=schema_a)
            repo_b = ResearchRepository(conn_b, schema=schema_b)
            docs_a = repo_a.list_documents(limit=500, visibility=INTERNAL_VISIBILITY)
            docs_b = repo_b.list_documents(limit=500, visibility=INTERNAL_VISIBILITY)

            key = lambda item: (item["external_id"], item["current_revision_hash"])
            self.assertEqual(sorted(map(key, docs_a)), sorted(map(key, docs_b)))
            self.assertEqual(len(docs_a), 36)
            self.assertEqual(resumed["stored_count"], one_shot["stored_count"])
            self.assertEqual(resumed["final_cursor"], one_shot["cursor_end"])
            self.assertGreater(len(resumed["batches"]), 1, "배치가 하나뿐이면 재개 검증이 아니다")

    # ---------------------------------------------------------------- 레거시 무접촉
    def test_seed_and_ingest_only_read_legacy_tables(self) -> None:
        with isolated_schema() as (conn, schema):
            recorder = StatementRecorder(conn)
            svc = ResearchIngestionService(schema=schema)
            seed = svc.seed_sources_from_telegram(recorder)
            recorder.commit()
            source_id = seed["registered"][0]["source_id"]
            svc.ingest_source(recorder, source_id, limit=5)
            recorder.commit()

            legacy_writes = recorder.writes_to(LEGACY_TABLES)
            self.assertEqual(legacy_writes, [], f"레거시 테이블 쓰기 발견: {legacy_writes}")
            self.assertTrue(recorder.statements_touching(("telegram_channel_posts",)))
            self.assertTrue(
                recorder.statements_touching(("research_documents",)),
                "AGI 신설 테이블에 쓰기가 전혀 기록되지 않았다",
            )

    # ---------------------------------------------------------------- T8 완전성
    def test_coverage_is_denominator_unknown_and_never_claims_completeness(self) -> None:
        with isolated_schema() as (conn, schema):
            svc, seed, by_channel = self._seed(conn, schema)
            source_id = by_channel[self.busiest]
            svc.ingest_source(conn, source_id, limit=3)
            conn.commit()

            # 공개 뷰는 `publish_summary='denied'`(기본값) 때문에 비어 있고, 그 사실이 note 로 설명된다.
            public = svc.documents_envelope(conn, source_id=source_id)
            self.assertTrue(public["applied"])
            self.assertEqual(public["items"], [])
            self.assertIn("publish_summary", public["note"])

            envelope = svc.documents_envelope(
                conn, source_id=source_id, visibility=INTERNAL_VISIBILITY
            )
            self.assertTrue(envelope["applied"])
            for mark in envelope["source_watermarks"]:
                self.assertEqual(mark["coverage"], "denominator_unknown")
            import json

            self.assertNotIn("완전", json.dumps(envelope["source_watermarks"], ensure_ascii=False))
            self.assertIn("coverage:denominator_unknown", envelope["unconfirmed_fields"])
            self.assertEqual(envelope["revision_hash_scope"], "body_only")

    def test_source_id_is_deterministic_and_seed_is_idempotent(self) -> None:
        with isolated_schema() as (conn, schema):
            svc, first, _ = self._seed(conn, schema)
            second = svc.seed_sources_from_telegram(conn)
            conn.commit()
            self.assertEqual(first["registered_count"], second["registered_count"])
            repo = ResearchRepository(conn, schema=schema)
            self.assertEqual(
                len(repo.list_sources(visibility=INTERNAL_VISIBILITY)), first["registered_count"]
            )
            # 공개 뷰는 기본 `publish_summary='denied'` 때문에 비어 있다(§9-4 fail-closed 기본값).
            self.assertEqual(repo.list_sources(visibility=PUBLIC_VISIBILITY), [])
            self.assertEqual(
                make_source_id("telegram_channel", "owner", "sunstudy1004"),
                make_source_id("telegram_channel", "owner", "sunstudy1004"),
            )


class PublicScopeAllowlistContractTests(unittest.TestCase):
    """§9-4 노출 allowlist — denylist 금지·미분류 fail-closed·스키마 드리프트 감지.

    Planner 지적(2026-10-11): `scope <> 'private_chat'` **denylist** 는 두 구멍이 있었다.
      ① 미분류(`other`) 소스가 `is_private=false`·`publish_summary='allowed'` 가 되면 공개로 샌다.
      ② 스키마에 새 scope 값이 추가되면 그 값을 조용히 공개한다.
    지금은 **allowlist**(`PUBLIC_SCOPES`) 이며, 이 클래스가 그 불변식을 문장·결과·스키마 세 층에서
    고정한다.
    """

    # ------------------------------------------------------------------ 순수 문장
    def test_public_scope_clause_is_an_allowlist_not_a_denylist(self) -> None:
        self.assertEqual(public_scope_clause(), ALLOWLIST_SQL)
        self.assertEqual(public_scope_clause(""), BARE_ALLOWLIST_SQL)
        self.assertEqual(public_scope_clause("d"), f"d.{BARE_ALLOWLIST_SQL}")
        for clause in source_visibility_clauses(PUBLIC_VISIBILITY) + document_visibility_clauses(
            PUBLIC_VISIBILITY
        ):
            if "scope" in clause:
                self.assertNotIn("<>", clause, f"scope 에 denylist 잔존: {clause}")
                self.assertIn(" IN (", clause, f"scope 조건이 allowlist 가 아니다: {clause}")
        self.assertEqual(source_visibility_clauses(PUBLIC_VISIBILITY)[0], BARE_ALLOWLIST_SQL)
        self.assertIn(ALLOWLIST_SQL, document_visibility_clauses(PUBLIC_VISIBILITY))
        self.assertEqual(source_visibility_clauses(INTERNAL_VISIBILITY), [])
        self.assertEqual(document_visibility_clauses(INTERNAL_VISIBILITY), [])

    def test_invalid_scope_constant_fails_closed_instead_of_building_sql(self) -> None:
        """allowlist 상수가 오염되면 조용히 문장을 만들지 않고 예외로 멈춘다."""
        with mock.patch.object(repository, "PUBLIC_SCOPES", ("public_channel'; DROP TABLE x --",)):
            with self.assertRaises(ValueError):
                public_scope_clause()

    # ------------------------------------------------------------------ SQL 의미론
    def test_unknown_and_null_scope_values_are_not_truthy_in_sql(self) -> None:
        """`NULL IN (…)` 이 참이 아니라는 PG 의미론에 의존한다(allowlist 가 NULL-safe 한 이유)."""
        with isolated_schema(migrate=False) as (conn, _schema):
            row = conn.execute(
                "SELECT (NULL::text IN ('public_channel','blog','report')) AS null_in,"
                " ('newsletter' IN ('public_channel','blog','report')) AS unknown_in,"
                " ('other' IN ('public_channel','blog','report')) AS other_in,"
                " ('blog' IN ('public_channel','blog','report')) AS blog_in"
            ).fetchone()
            self.assertIsNone(row["null_in"], "NULL 은 참도 거짓도 아니어야 한다 → WHERE 에서 배제")
            self.assertFalse(row["unknown_in"])
            self.assertFalse(row["other_in"])
            self.assertTrue(row["blog_in"])

    # ------------------------------------------------------------------ 스키마 드리프트
    def _scope_check_values(self, conn, schema: str) -> tuple[str, list[str]]:
        rows = conn.execute(
            "SELECT c.conname, pg_get_constraintdef(c.oid) AS def "
            "FROM pg_constraint c "
            "JOIN pg_class t ON t.oid = c.conrelid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace "
            "WHERE n.nspname = %s AND t.relname = %s AND c.contype = 'c'",
            (schema, "research_sources"),
        ).fetchall()
        for row in rows:
            definition = row["def"]
            if "scope" in definition:
                return row["conname"], re.findall(r"'([a-z_]+)'", definition)
        raise AssertionError(f"research_sources 에 scope CHECK 제약이 없다: {rows}")

    def test_schema_scope_values_minus_allowlist_is_the_named_non_public_set(self) -> None:
        """스키마에 새 scope 가 생기면 이 테스트가 깨져 **공개 여부 결정을 강제**한다."""
        with isolated_schema() as (conn, schema):
            _name, schema_scopes = self._scope_check_values(conn, schema)
            self.assertEqual(
                set(schema_scopes) - set(PUBLIC_SCOPES),
                set(NON_PUBLIC_SCOPES),
                "스키마 CHECK 의 scope 값과 공개 allowlist 의 차집합이 바뀌었다 — "
                "새 scope 를 공개할지 PUBLIC_SCOPES/NON_PUBLIC_SCOPES 에서 결정하세요.",
            )
            self.assertEqual(set(PUBLIC_SCOPES) & set(NON_PUBLIC_SCOPES), set())
            for scope in schema_scopes:
                self.assertEqual(scope, scope.strip().lower())
            self.assertEqual(len(schema_scopes), len(set(schema_scopes)), "CHECK 값에 중복이 있다")

    def test_scope_added_to_the_schema_without_a_decision_stays_private(self) -> None:
        """CHECK 를 넓혀 새 scope 를 넣어도(스키마 드리프트) 공개로 승격되지 않는다.

        allowlist 는 스키마 CHECK 에 의존하지 않는다 — 이 테스트가 그 사실을 실측한다.
        """
        with isolated_schema() as (conn, schema):
            constraint, schema_scopes = self._scope_check_values(conn, schema)
            conn.execute(f'ALTER TABLE "{schema}".research_sources DROP CONSTRAINT {constraint}')
            conn.commit()

            # 드리프트 감지 시나리오: 새 값이 DDL 에 추가되었다면 위 가드가 깨졌을 것이다.
            widened = set(schema_scopes) | {"newsletter"}
            self.assertNotEqual(
                widened - set(PUBLIC_SCOPES),
                set(NON_PUBLIC_SCOPES),
                "가드가 새 scope 를 통과시킨다(드리프트 감지 실패)",
            )

            repo = ResearchRepository(conn, schema=schema)
            svc = ResearchIngestionService(schema=schema)
            source_id = repo.ensure_source(
                source_kind="telegram_channel",
                source_owner="owner",
                source_native_id="drift-source",
                scope="newsletter",
            )
            conn.execute(
                f'UPDATE "{schema}".research_sources SET publish_summary = %s WHERE source_id = %s',
                ("allowed", source_id),
            )
            conn.commit()
            run_id = repo.begin_run(source_id=source_id, run_kind="seed")
            conn.commit()
            svc._store_post(repo, source_id, "drift-source", False, _post(1, "새 등급 글"), run_id, "metadata_only")
            conn.commit()
            document_id = make_document_id(source_id, "1")

            # 전제: 행은 존재하고 관리자 뷰에는 보인다.
            self.assertIsNotNone(repo.get_document(document_id, visibility=INTERNAL_VISIBILITY))
            # 결론: 공개 3경로 어디에도 없다.
            self.assertEqual(repo.list_documents(visibility=PUBLIC_VISIBILITY), [])
            self.assertEqual(repo.get_document(document_id, visibility=PUBLIC_VISIBILITY), None)
            self.assertEqual(repo.list_sources(visibility=PUBLIC_VISIBILITY), [])

    def test_denylist_would_still_leak_so_the_allowlist_is_load_bearing(self) -> None:
        """변이 검증 — 옛 denylist 문장으로 되돌리면 실제로 샌다(가짜 수정 방지).

        이 테스트가 없으면 `document_visibility_clauses` 가 allowlist 인지 denylist 인지
        구분하지 못한 채 '0건' 만 보고 통과할 수 있다.
        """
        with isolated_schema() as (conn, schema):
            repo = ResearchRepository(conn, schema=schema)
            svc = ResearchIngestionService(schema=schema)
            source_id = repo.ensure_source(
                source_kind="telegram_channel",
                source_owner="owner",
                source_native_id="mutation-other",
                scope="other",
            )
            conn.execute(
                f'UPDATE "{schema}".research_sources SET publish_summary = %s WHERE source_id = %s',
                ("allowed", source_id),
            )
            conn.commit()
            run_id = repo.begin_run(source_id=source_id, run_kind="seed")
            conn.commit()
            svc._store_post(repo, source_id, "mutation-other", False, _post(1, "미분류 글"), run_id, "metadata_only")
            conn.commit()
            document_id = make_document_id(source_id, "1")

            # 현재(allowlist): 목록·상세 모두 0건.
            self.assertEqual(repo.list_documents(visibility=PUBLIC_VISIBILITY), [])
            self.assertIsNone(repo.get_document(document_id, visibility=PUBLIC_VISIBILITY))

            def _old_denylist(visibility, **_kwargs):
                if visibility == PUBLIC_VISIBILITY:
                    return ["d.is_private = false", "s.publish_summary <> 'denied'"]
                return []

            with mock.patch.object(repository, "document_visibility_clauses", _old_denylist):
                leaked = repo.list_documents(visibility=PUBLIC_VISIBILITY)
                self.assertEqual(
                    [row["document_id"] for row in leaked],
                    [document_id],
                    "denylist 로 되돌려도 0건이면 이 테스트가 검증력을 잃는다(데이터가 없다)",
                )
                self.assertIsNotNone(
                    repo.get_document(document_id, visibility=PUBLIC_VISIBILITY),
                    "denylist 로 되돌렸을 때의 상세 누출이 재현되지 않는다",
                )

    def test_public_watermarks_cover_only_allowlisted_scopes(self) -> None:
        """watermark 는 소스 목록 위에 서므로 같은 allowlist 를 받는다(별도 경로 검증)."""
        with isolated_schema() as (conn, schema):
            repo = ResearchRepository(conn, schema=schema)
            svc = ResearchIngestionService(schema=schema)
            for scope in (*PUBLIC_SCOPES, *NON_PUBLIC_SCOPES):
                source_id = repo.ensure_source(
                    source_kind="telegram_channel",
                    source_owner="owner",
                    source_native_id=f"wm-{scope}",
                    scope=scope,
                )
                conn.execute(
                    f'UPDATE "{schema}".research_sources '
                    "SET publish_summary = 'allowed' WHERE source_id = %s",
                    (source_id,),
                )
                conn.commit()
                run_id = repo.begin_run(source_id=source_id, run_kind="seed")
                conn.commit()
                svc._store_post(repo, source_id, f"wm-{scope}", False, _post(1, "글"), run_id, "metadata_only")
                conn.commit()

            marks = repo.source_watermarks(visibility=PUBLIC_VISIBILITY)
            self.assertEqual({m["scope"] for m in marks}, set(PUBLIC_SCOPES))
            self.assertEqual(len(marks), len(PUBLIC_SCOPES))
            self.assertEqual(
                len(repo.source_watermarks(visibility=INTERNAL_VISIBILITY)),
                len(PUBLIC_SCOPES) + len(NON_PUBLIC_SCOPES),
            )


if __name__ == "__main__":
    unittest.main()
