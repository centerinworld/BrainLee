"""`/api/agi/*` 권한 경계·무부작용 조회 계약 테스트 (routes/agi.py).

계약 정본: `docs/AGI_01_SOURCE_TIME_AUTH_CONTRACT_20261011.md`
  * §9-2 GET 은 저장된 결과만 반환한다(부작용 0)        → 수용기준 T11
  * §9-3 응답 봉투에 as_of / snapshot_generation_time /
         source_watermarks / quality_summary / unconfirmed_fields 포함 → T12
  * §9-4 **공개 화면에는 `publish_summary != 'denied'` 근거만** 노출하고, 개인 대화·비공개
         원문은 **인증된 연구 화면**에 둔다  ← Planner P0 BLOCK(2026-10-11)의 회귀 테스트

권한 경계는 두 겹으로 검증한다.
  ① SQL 단계 — 공개 경로가 `include_private` 같은 외부 파라미터를 받지 않고, 개인 대화·
     `denied` 를 쿼리에서 배제한다(인증·터널과 무관하게 성립).
  ② 게이트 단계 — 관리자 경로가 `security_gate` 에서 owner 등급으로 분류되고, 핸들러가
     `_owner_ok` 를 다시 확인한다.

라우터는 **읽기 전용 연결**로만 열린다. 테스트가 임시 스키마를 가리켜도 쓰기가 불가능하고,
코드가 실수로 쓰기를 시도하면 조용히 성공하는 대신 오류로 드러난다.

실행:
    ./venv/bin/python -m pytest tests/test_agi_documents_api.py -v
"""

from __future__ import annotations

import unittest
from unittest import mock

import security_gate as sg
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.agi as agi_route
from agi.db import connect_primary
from agi.repository import (
    INTERNAL_VISIBILITY,
    NON_PUBLIC_SCOPES,
    PUBLIC_SCOPES,
    PUBLIC_VISIBILITY,
    ResearchRepository,
    make_document_id,
)
from agi.service import ResearchIngestionService
from tests.agi_test_support import StatementRecorder, isolated_schema

ENVELOPE_KEYS = (
    "items",
    "as_of",
    "snapshot_generation_time",
    "source_watermarks",
    "quality_summary",
    "unconfirmed_fields",
    "public_scopes",
)


def _post(message_id: int, text: str) -> dict:
    return {
        "message_id": message_id,
        "text": text,
        "msg_date": "2026-01-01 00:00:00",
        "collected_at": "2026-01-01T00:00:05",
        "photo_path": "",
        "file_name": "",
    }


async def _allow_owner(request) -> None:  # 관리자 인증을 통과한 상태를 흉내낸다
    return None


class _ApiFixture(unittest.TestCase):
    def _client(self, schema: str, *, authorized: bool = True) -> TestClient:
        """라우터를 임시 스키마로 향하게 한 TestClient(읽기 전용 연결은 그대로).

        `authorized=False` 면 **실제** `require_owner`(security_gate owner 판정)를 쓴다.
        """
        app = FastAPI()
        app.include_router(agi_route.router, prefix="/api/agi", tags=["agi"])
        patches = [
            mock.patch.multiple(
                agi_route,
                connect_readonly=lambda: connect_primary(readonly=True, timeout=30),
                get_service=lambda: ResearchIngestionService(schema=schema),
            )
        ]
        if authorized:
            patches.append(mock.patch.object(agi_route, "require_owner", _allow_owner))
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        return TestClient(app)

    @staticmethod
    def _prepare(conn, schema: str, *, publish_summary: str | None = None) -> dict:
        svc = ResearchIngestionService(schema=schema)
        repo = ResearchRepository(conn, schema=schema)
        seed = svc.seed_sources_from_telegram(conn)
        conn.commit()
        source_id = seed["registered"][0]["source_id"]
        if publish_summary is not None:
            conn.execute(
                f'UPDATE "{schema}".research_sources SET publish_summary = %s WHERE source_id = %s',
                (publish_summary, source_id),
            )
        svc.ingest_source(conn, source_id, limit=3)
        conn.commit()

        private_source = repo.ensure_source(
            source_kind="telegram_channel",
            source_owner="owner",
            source_native_id="123456789",
            scope="private_chat",
        )
        run_id = repo.begin_run(source_id=private_source, run_kind="seed")
        conn.commit()
        svc._store_post(repo, private_source, "123456789", True, _post(1, "개인 대화"), run_id, "metadata_only")
        conn.commit()
        private_document_id = repo.list_documents(
            visibility=INTERNAL_VISIBILITY, source_id=private_source, limit=1
        )[0]["document_id"]
        return {
            "source_id": source_id,
            "private_source": private_source,
            "private_document_id": private_document_id,
        }


class PublicVisibilityTests(_ApiFixture):
    """§9-4 — 비인증 응답에 비공개·미승인 원문이 절대 포함되지 않는다."""

    def test_public_list_excludes_denied_and_private(self) -> None:
        with isolated_schema() as (conn, schema):
            ctx = self._prepare(conn, schema)  # 기본 publish_summary = 'denied'
            client = self._client(schema, authorized=False)

            response = client.get("/api/agi/documents", params={"limit": 500})
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["visibility"], "public")
            self.assertEqual(payload["items"], [])
            self.assertEqual(payload["count"], 0)
            for item in payload["items"]:
                self.assertNotEqual(item["publish_summary"], "denied")
                self.assertFalse(item["is_private"])
            self.assertEqual(
                [w for w in payload["source_watermarks"] if w["source_id"] == ctx["private_source"]],
                [],
                "개인 대화 소스가 공개 watermark 목록에 노출되었다",
            )

            scoped = client.get(
                "/api/agi/documents", params={"source_id": ctx["source_id"], "limit": 500}
            )
            self.assertEqual(scoped.json()["items"], [])

    def test_public_endpoint_ignores_private_scope_query_parameter(self) -> None:
        """`include_private` 는 더 이상 존재하지 않는다 — 넘겨도 무시된다(권한 상승 차단)."""
        with isolated_schema() as (conn, schema):
            ctx = self._prepare(conn, schema)
            client = self._client(schema, authorized=False)
            for value in ("true", "1", "yes"):
                response = client.get(
                    "/api/agi/documents",
                    params={"include_private": value, "source_id": ctx["private_source"]},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.json()["items"], [], f"include_private={value} 가 반영되었다"
                )

    def test_public_detail_hides_private_document(self) -> None:
        with isolated_schema() as (conn, schema):
            ctx = self._prepare(conn, schema)
            client = self._client(schema, authorized=False)
            response = client.get(f"/api/agi/documents/{ctx['private_document_id']}")
            self.assertEqual(response.status_code, 404)

    def test_public_list_shows_documents_once_publish_rights_are_granted(self) -> None:
        """필터가 '무조건 0건'이 아니라 계약의 `publish_summary != denied` 규칙임을 확인한다."""
        with isolated_schema() as (conn, schema):
            ctx = self._prepare(conn, schema, publish_summary="owner_approval")
            client = self._client(schema, authorized=False)
            payload = client.get(
                "/api/agi/documents", params={"source_id": ctx["source_id"], "limit": 500}
            ).json()
            self.assertEqual(payload["count"], 3)
            for item in payload["items"]:
                self.assertEqual(item["publish_summary"], "owner_approval")
                self.assertFalse(item["is_private"])

    def test_public_envelope_has_contract_keys_and_hash_scope(self) -> None:
        with isolated_schema() as (conn, schema):
            self._prepare(conn, schema, publish_summary="allowed")
            client = self._client(schema, authorized=False)
            payload = client.get("/api/agi/documents").json()
            for key in ENVELOPE_KEYS:
                self.assertIn(key, payload)
            self.assertTrue(payload["applied"])
            self.assertEqual(payload["revision_hash_scope"], "body_only")
            self.assertIn("본문 전용", payload["revision_hash_scope_note"])
            self.assertIn("publish_summary", payload["note"])
            self.assertEqual(payload["count"], len(payload["items"]))
            self.assertEqual(payload["as_of"] is not None, True)
            self.assertEqual(set(payload["coverage_summary"]), {"denominator_unknown"})

    def test_missing_tables_report_not_applied_instead_of_failing(self) -> None:
        """마이그레이션 미적용 상태에서도 500 이 아니라 정직한 상태를 돌려준다."""
        with isolated_schema(migrate=False) as (conn, schema):
            client = self._client(schema, authorized=False)
            response = client.get("/api/agi/documents")
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertFalse(payload["applied"])
            self.assertEqual(payload["items"], [])
            for key in ENVELOPE_KEYS:
                self.assertIn(key, payload)

    def test_paging_bounds_are_enforced(self) -> None:
        with isolated_schema() as (conn, schema):
            self._prepare(conn, schema)
            client = self._client(schema, authorized=False)
            self.assertEqual(client.get("/api/agi/documents", params={"limit": 0}).status_code, 422)
            self.assertEqual(client.get("/api/agi/documents", params={"limit": 501}).status_code, 422)
            self.assertEqual(client.get("/api/agi/documents", params={"offset": -1}).status_code, 422)


class PublicScopeAllowlistTests(_ApiFixture):
    """§9-4 fail-closed — 공개는 **명시 allowlist**(`PUBLIC_SCOPES`)로만 성립한다.

    Planner 지적(2026-10-11): 종전 `document_visibility_clauses()` 는 `scope <> 'private_chat'`
    라는 **denylist** 였다. 그래서 `other`(공개 미증명 미분류) 소스가 `is_private=false` +
    `publish_summary='allowed'` 가 되면 공개될 수 있었다. 지금은 allowlist 이므로 그 조합에서도
    **목록·상세·watermark 어디에도** 나오지 않아야 한다.
    """

    def _store_unclassified(
        self,
        conn,
        schema: str,
        *,
        scope: str,
        publish_summary: str = "allowed",
        is_private: bool = False,
        native: str | None = None,
        message_id: int = 7,
    ) -> dict:
        """소스 1개 + 글 1건을 만든다(상태값을 직접 조작해 경계만 따로 검증한다).

        `native` 를 조합마다 다르게 주는 이유: `store_document` 의 `ON CONFLICT ... DO UPDATE`
        는 `updated_at` 만 갱신하므로(멱등 — 계약 §5) 같은 (source, external_id) 를 재사용하면
        `is_private` 를 바꿔 가며 검증할 수 없다.
        """
        repo = ResearchRepository(conn, schema=schema)
        svc = ResearchIngestionService(schema=schema)
        native = native or f"scope-{scope}"
        source_id = repo.ensure_source(
            source_kind="telegram_channel",
            source_owner="owner",
            source_native_id=native,
            scope=scope,
        )
        conn.execute(
            f'UPDATE "{schema}".research_sources SET publish_summary = %s, scope = %s '
            "WHERE source_id = %s",
            (publish_summary, scope, source_id),
        )
        conn.commit()
        run_id = repo.begin_run(source_id=source_id, run_kind="seed")
        conn.commit()
        svc._store_post(
            repo, source_id, native, is_private, _post(message_id, "미분류 소스 글"), run_id, "metadata_only"
        )
        conn.commit()
        return {
            "source_id": source_id,
            "document_id": make_document_id(source_id, str(message_id)),
            "scope": scope,
        }

    def test_other_scope_never_appears_in_list_detail_or_watermarks(self) -> None:
        """명시 allowlist 회귀 — `other` + `is_private=false` + `publish_summary='allowed'`."""
        with isolated_schema() as (conn, schema):
            ctx = self._store_unclassified(conn, schema, scope="other")
            repo = ResearchRepository(conn, schema=schema)
            client = self._client(schema, authorized=False)  # 공개 경로 = 무인증
            admin_client = self._client(schema, authorized=True)

            # 전제: 행은 실제로 만들어졌고 관리자 뷰에는 보인다 → 0건이 '데이터 없음'이 아니다.
            admin = admin_client.get("/api/agi/admin/documents", params={"limit": 500}).json()
            admin_ids = {item["document_id"] for item in admin["items"]}
            self.assertIn(ctx["document_id"], admin_ids, "전제 실패: 문서가 생성되지 않았다")
            admin_item = next(i for i in admin["items"] if i["document_id"] == ctx["document_id"])
            self.assertEqual(admin_item["source_scope"], "other")
            self.assertEqual(admin_item["publish_summary"], "allowed")
            self.assertFalse(admin_item["is_private"])

            # ① 목록
            listing = client.get("/api/agi/documents", params={"limit": 500}).json()
            self.assertEqual(listing["items"], [], "other 소스 문서가 공개 목록에 노출되었다")
            self.assertEqual(listing["count"], 0)
            scoped = client.get(
                "/api/agi/documents", params={"source_id": ctx["source_id"], "limit": 500}
            ).json()
            self.assertEqual(scoped["items"], [], "source_id 를 지정해도 노출되었다")

            # ② 상세
            self.assertEqual(
                client.get(f"/api/agi/documents/{ctx['document_id']}").status_code,
                404,
                "other 소스 문서가 공개 상세로 노출되었다",
            )

            # ③ watermark
            self.assertEqual(
                [w for w in listing["source_watermarks"] if w["source_id"] == ctx["source_id"]],
                [],
                "other 소스가 공개 watermark 에 노출되었다",
            )
            self.assertEqual(repo.list_sources(visibility=PUBLIC_VISIBILITY), [])

    def test_unclassified_scope_is_hidden_in_every_state_combination(self) -> None:
        """`scope`·`publish_summary`·`is_private` 12조합 전부에서 공개 0건(3값 교차 검증)."""
        with isolated_schema() as (conn, schema):
            repo = ResearchRepository(conn, schema=schema)
            client = self._client(schema, authorized=False)
            checked = 0
            for scope in NON_PUBLIC_SCOPES:
                for publish_summary in ("denied", "owner_approval", "allowed"):
                    for is_private in (False, True):
                        with self.subTest(
                            scope=scope, publish_summary=publish_summary, is_private=is_private
                        ):
                            native = f"combo-{scope}-{publish_summary}-{int(is_private)}"
                            ctx = self._store_unclassified(
                                conn,
                                schema,
                                scope=scope,
                                publish_summary=publish_summary,
                                is_private=is_private,
                                native=native,
                            )
                            self.assertEqual(
                                repo.list_documents(
                                    visibility=PUBLIC_VISIBILITY, source_id=ctx["source_id"]
                                ),
                                [],
                            )
                            self.assertEqual(
                                client.get(f"/api/agi/documents/{ctx['document_id']}").status_code,
                                404,
                            )
                            self.assertEqual(
                                [
                                    w
                                    for w in repo.source_watermarks(visibility=PUBLIC_VISIBILITY)
                                    if w["source_id"] == ctx["source_id"]
                                ],
                                [],
                            )
                            checked += 1
            self.assertEqual(checked, 2 * 3 * 2, "조합 수가 줄었다(검증이 약해졌다)")

    def test_allowlisted_scopes_remain_public_when_rights_are_granted(self) -> None:
        """양성 대조 — allowlist 안 scope 는 같은 조건에서 **공개된다**(빈 결과 고정이 아님)."""
        with isolated_schema() as (conn, schema):
            client = self._client(schema, authorized=False)
            seen = []
            for scope in PUBLIC_SCOPES:
                ctx = self._store_unclassified(
                    conn, schema, scope=scope, publish_summary="allowed", native=f"allow-{scope}"
                )
                payload = client.get(
                    "/api/agi/documents",
                    params={"source_id": ctx["source_id"], "limit": 500},
                ).json()
                self.assertEqual(payload["count"], 1, scope)
                self.assertEqual(payload["items"][0]["source_scope"], scope)
                self.assertEqual(payload["items"][0]["publish_summary"], "allowed")
                detail = client.get(f"/api/agi/documents/{ctx['document_id']}")
                self.assertEqual(detail.status_code, 200, scope)
                self.assertEqual(detail.json()["item"]["source_scope"], scope)
                seen.append(scope)
            self.assertEqual(seen, list(PUBLIC_SCOPES))

    def test_public_queries_carry_the_allowlist_in_sql(self) -> None:
        """결과가 아니라 **문장**으로 고정한다 — 목록·상세·소스 3경로 모두 allowlist 를 싣는다."""
        with isolated_schema() as (conn, schema):
            recorder = StatementRecorder(conn)
            repo = ResearchRepository(recorder, schema=schema)
            recorder.statements.clear()

            repo.list_documents(visibility=PUBLIC_VISIBILITY)
            repo.get_document("nonexistent", visibility=PUBLIC_VISIBILITY)
            repo.list_sources(visibility=PUBLIC_VISIBILITY)

            public_sql = [s for s in recorder.statements if "scope IN (" in s]
            self.assertEqual(
                len(public_sql), 3, f"공개 3경로 중 allowlist 누락: {recorder.statements}"
            )
            for sql in public_sql:
                self.assertIn("scope IN ('public_channel', 'blog', 'report')", sql)
            self.assertNotIn(
                "scope <> 'private_chat'",
                "\n".join(recorder.statements),
                "denylist 가 남아 있다(allowlist 로 대체되어야 한다)",
            )

            recorder.statements.clear()
            repo.list_documents(visibility=INTERNAL_VISIBILITY)
            repo.list_sources(visibility=INTERNAL_VISIBILITY)
            internal_sql = "\n".join(recorder.statements)
            self.assertIn("research_documents", internal_sql, "관리자 조회가 실행되지 않았다")
            self.assertNotIn(
                "scope IN (", internal_sql, "관리자(owner) 경로에 공개 allowlist 가 걸렸다"
            )

    def test_public_response_exposes_the_allowlist_and_never_a_foreign_scope(self) -> None:
        """공개 응답만 보고도 경계를 감사할 수 있어야 한다 — `public_scopes` + 항목의 `source_scope`."""
        with isolated_schema() as (conn, schema):
            self._store_unclassified(conn, schema, scope="other")
            self._store_unclassified(
                conn, schema, scope="public_channel", publish_summary="allowed", native="ok-src"
            )
            client = self._client(schema, authorized=False)
            payload = client.get("/api/agi/documents", params={"limit": 500}).json()
            self.assertEqual(payload["public_scopes"], list(PUBLIC_SCOPES))
            self.assertTrue(payload["items"], "양성 대조군이 사라졌다")
            for item in payload["items"]:
                self.assertIn(item["source_scope"], PUBLIC_SCOPES)
            for mark in payload["source_watermarks"]:
                self.assertIn(mark["scope"], PUBLIC_SCOPES)


class AdminVisibilityTests(_ApiFixture):
    """인증된 연구 화면 — 비공개·미승인 원문을 볼 수 있는 유일한 경로."""

    def test_admin_routes_require_auth(self) -> None:
        with isolated_schema() as (conn, schema):
            ctx = self._prepare(conn, schema)
            client = self._client(schema, authorized=False)

            for path in (
                "/api/agi/admin/documents",
                f"/api/agi/admin/documents/{ctx['private_document_id']}",
            ):
                response = client.get(path)
                self.assertEqual(response.status_code, 401, path)
                self.assertEqual(response.json()["detail"]["reason"], "owner_required")

            # 같은 문서를 공개 경로로도 볼 수 없다(두 경로가 서로를 우회하지 못한다).
            self.assertEqual(
                client.get(f"/api/agi/documents/{ctx['private_document_id']}").status_code, 404
            )

    def test_admin_routes_are_owner_level_in_the_existing_gate(self) -> None:
        """경로 자체가 security_gate 에서 owner 로 분류되어야 한다(회귀 방지)."""
        for path in ("/api/agi/admin/documents", "/api/agi/admin/documents/abc123"):
            self.assertEqual(sg.access_level("GET", path), "owner", path)
            self.assertTrue(sg.is_protected("GET", path), path)
        for path in ("/api/agi/documents", "/api/agi/documents/abc123", "/api/agi/status"):
            self.assertEqual(sg.access_level("GET", path), "public", path)

    def test_admin_view_exposes_private_and_denied_when_authorized(self) -> None:
        with isolated_schema() as (conn, schema):
            ctx = self._prepare(conn, schema)
            client = self._client(schema, authorized=True)

            payload = client.get("/api/agi/admin/documents", params={"limit": 500}).json()
            self.assertEqual(payload["visibility"], "internal")
            self.assertNotIn("note", payload)

            private_items = [
                item
                for item in payload["items"]
                if item["document_id"] == ctx["private_document_id"]
            ]
            self.assertEqual(len(private_items), 1)
            self.assertTrue(private_items[0]["is_private"])
            self.assertEqual(private_items[0]["publish_summary"], "denied")

            denied_items = [
                item
                for item in payload["items"]
                if item["publish_summary"] == "denied" and not item["is_private"]
            ]
            self.assertTrue(denied_items, "denied 소스 문서가 관리자 뷰에도 없다")

            detail = client.get(f"/api/agi/admin/documents/{ctx['private_document_id']}")
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.json()["visibility"], "internal")
            self.assertTrue(detail.json()["item"]["is_private"])
            self.assertEqual(len(detail.json()["revisions"]), 1)
            self.assertEqual(detail.json()["revisions"][0]["change_kind"], "new")
            self.assertEqual(detail.json()["revision_hash_scope"], "body_only")

    def test_get_has_no_side_effects(self) -> None:
        """T11: GET 전후 지문(행수 + 최대 ID)이 동일해야 한다 — 공개·관리자 양쪽."""
        with isolated_schema() as (conn, schema):
            self._prepare(conn, schema)
            repo = ResearchRepository(conn, schema=schema)
            conn.commit()
            before = repo.snapshot_fingerprint()

            client = self._client(schema, authorized=True)
            for _ in range(3):
                self.assertEqual(client.get("/api/agi/documents").status_code, 200)
                self.assertEqual(
                    client.get(
                        "/api/agi/admin/documents", params={"limit": 2, "offset": 1}
                    ).status_code,
                    200,
                )
            self.assertEqual(client.get("/api/agi/status").status_code, 200)
            self.assertEqual(client.get("/api/agi/documents/nope").status_code, 404)
            self.assertEqual(client.get("/api/agi/admin/documents/nope").status_code, 404)

            self.assertEqual(before, repo.snapshot_fingerprint())


class AgiRouteConnectionTests(unittest.TestCase):
    def test_route_opens_a_read_only_transaction(self) -> None:
        """라우터의 실제 연결 팩토리 — 읽기 전용이어야 한다(쓰기 불가 = 무부작용 1차 보증)."""
        conn = agi_route.connect_readonly()
        try:
            self.assertEqual(conn.execute("SHOW transaction_read_only").fetchone()[0], "on")
        finally:
            conn.close()

    def test_status_endpoint_is_read_only_and_does_not_apply(self) -> None:
        app = FastAPI()
        app.include_router(agi_route.router, prefix="/api/agi", tags=["agi"])
        client = TestClient(app)
        response = client.get("/api/agi/status")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["namespace"], "/api/agi")
        self.assertFalse(payload["live_apply_performed_by_this_process"])
        self.assertEqual(payload["owner_routes"], ["/api/agi/admin/documents"])
        self.assertIn("/api/agi/company-reports", payload["public_routes"])
        self.assertTrue(payload["migrations"])
        self.assertEqual(payload["migrations"][0]["version"], "0001_research_core")


if __name__ == "__main__":
    unittest.main()
