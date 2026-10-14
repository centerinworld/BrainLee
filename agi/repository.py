"""AGI 원문 보관 저장소 — 멱등 upsert 만 노출한다.

계약 AGI-01 §5(멱등키)·§5-2(실행 원장 ↔ 엔티티)를 코드로 고정한다.
  * 소스/문서/revision 은 자연키가 동일하면 **추가 0**(DB UNIQUE + `ON CONFLICT`).
  * `ingestion_runs`(실행 원장)는 매 실행 새 행이다(중복 제거하지 않는다).

연결은 호출자가 준다(`conn`) — 프로덕션은 `agi.db.connect_primary()`,
검증은 임시 스키마를 가리키는 연결.

⚠️ SQL 은 psycopg 네이티브 `%s` 바인딩을 쓴다. `db_compat` 호환 레이어도 `%s` 를 그대로
통과시키므로 두 경로에서 같은 문장이 실행된다(`?` 로 쓰면 호환 레이어만 통과한다).
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

from agi.db import qualify

PUBLIC_VISIBILITY = "public"
INTERNAL_VISIBILITY = "internal"
VISIBILITIES = (PUBLIC_VISIBILITY, INTERNAL_VISIBILITY)

# ---------------------------------------------------------------- 원문 보관 권한(계약 §7)
# `research_sources.store_original` 3값. 'yes' 만 본문을 그대로 보관한다.
STORE_ORIGINAL_FULL = "yes"
STORE_ORIGINAL_METADATA_ONLY = "metadata_only"
STORE_ORIGINAL_NONE = "no"
STORE_ORIGINAL_VALUES = (STORE_ORIGINAL_FULL, STORE_ORIGINAL_METADATA_ONLY, STORE_ORIGINAL_NONE)


def should_create_revision_for_policy(store_original: str) -> bool:
    """`store_original='no'` 는 revision 행 자체를 만들지 않는다(최소 감사 정보만 유지).

    (2026-10-13 외부 검토 R1) 변경 이력·본문을 전혀 남기지 않는 쪽이 'no' 의 계약 의미다 —
    문서 존재(제목·발행시각 등 발견 단계 사실)는 `research_documents` 에 이미 별도로 남는다.
    """
    if store_original not in STORE_ORIGINAL_VALUES:
        raise ValueError(f"invalid store_original: {store_original!r}")
    return store_original != STORE_ORIGINAL_NONE


def resolve_store_original_body(store_original: str, body: str | None) -> str | None:
    """`store_original` 정책에 따라 **저장 계층에 넘길 본문**을 결정한다(계약 §7).

    (2026-10-13 외부 검토 R1) 'yes' 만 본문을 그대로 돌려준다. 'metadata_only' 는 변경
    감지용 해시를 계속 계산할 수 있도록 호출자가 메모리에서 `body` 를 그대로 쓰되, **저장
    계층(`store_revision`)에는 이 함수가 돌려주는 값만** 넘겨야 한다 — 본문을 DB 밖에서는
    계속 들고 있어도 되지만, 보관 테이블에는 닿지 않는다.
    """
    if store_original not in STORE_ORIGINAL_VALUES:
        raise ValueError(f"invalid store_original: {store_original!r}")
    return body if store_original == STORE_ORIGINAL_FULL else None

# ---------------------------------------------------------------- 공개 scope allowlist
# 계약 §9-4 의 "공개 화면 노출"은 **명시 allowlist** 로만 성립한다(fail-closed).
#
# 스키마(`m0001_research_core`)의 `scope` CHECK 는 5종이다:
#     public_channel | private_chat | blog | report | other
# 그중 공개 근거로 인정하는 것은 아래 3종뿐이다. `private_chat`(개인 대화)과
# `other`(공개임을 증명할 수 없는 미분류)는 **어떤 상태값 조합에서도**(is_private=false,
# publish_summary='allowed' 여부와 무관하게) 공개되지 않는다.
#
# 왜 denylist 가 아니라 allowlist 인가: denylist(`scope <> 'private_chat'`)는 스키마에 새
# scope 값이 추가되는 순간 그 값을 **조용히 공개**한다. allowlist 는 모르는 값을 배제하므로
# 새 값이 생겨도 공개로 승격되지 않는다(NULL scope 도 `NULL IN (…)` 이 참이 아니라 배제된다).
# 새 값을 공개하려면 이 상수를 고치는 것이 유일한 경로이며, 회귀 테스트가
# 스키마 CHECK 값과 이 allowlist 의 차집합이 `NON_PUBLIC_SCOPES` 와 정확히 같음을 강제한다.
PUBLIC_SCOPES = ("public_channel", "blog", "report")

# 공개로 **인정하지 않는** scope 중 사람이 이름을 붙인 것(나머지는 자동 fail-closed).
NON_PUBLIC_SCOPES = ("private_chat", "other")

_SCOPE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def public_scope_clause(alias: str = "s") -> str:
    """공개 노출용 `scope` allowlist SQL 조각(fail-closed).

    값은 모듈 상수(사용자 입력 아님)이므로 리터럴로 인라인하되 형식을 먼저 강제한다.
    """
    for scope in PUBLIC_SCOPES:
        if not _SCOPE_RE.match(scope):
            raise ValueError(f"invalid public scope constant: {scope!r}")
    column = f"{alias}.scope" if alias else "scope"
    literals = ", ".join(f"'{scope}'" for scope in PUBLIC_SCOPES)
    return f"{column} IN ({literals})"


def _check_visibility(visibility: str) -> bool:
    if visibility == INTERNAL_VISIBILITY:
        return False
    if visibility != PUBLIC_VISIBILITY:
        raise ValueError(f"invalid visibility: {visibility!r}")
    return True


def source_visibility_clauses(visibility: str) -> list[str]:
    """`research_sources` 단독 조회용 노출 조건(계약 §9-4).

    `scope` 는 **allowlist**(`public_scope_clause`)로만 통과한다 — `private_chat` 은
    allowlist 에 없으므로 배제되고, `other`(미분류)도 마찬가지다. denylist(`<> 'private_chat'`)
    를 쓰지 않는 이유는 `public_scope_clause` 주석 참조.
    """
    if not _check_visibility(visibility):
        return []
    return [public_scope_clause(""), "publish_summary <> 'denied'"]


def document_visibility_clauses(
    visibility: str, *, document_alias: str = "d", source_alias: str = "s"
) -> list[str]:
    """문서+소스 조인 조회용 노출 조건 — **SQL 단계에서** 비공개 원문을 배제한다.

    네 조건이 모두 필요하다(하나만으로는 부족하다).
      ① `is_private = false` — 글 단위 비공개 표시(개인 대화 글).
      ② `scope` allowlist — 소스 등급이 공개 가능한 종류인가(`other`/미분류 배제).
      ③ `publish_summary <> 'denied'` — 소스의 공개 권한이 실제로 열렸는가.
      ④ `access_state = 'ok'` — 글이 지금 살아 있는가(AGI-03).

    ④ 의 근거: 삭제·비공개·접근 불가 글의 **본문 revision 은 계보를 위해 보존**하되
    (계약 §5-2: 행을 지우지 않는다), 보존과 **공개**는 다른 문제다. tombstone 된 글을
    공개 목록/상세에 계속 노출하면 원작자가 내린 삭제 결정이 화면에 반영되지 않는다.
    `access_state` 는 4종(`ok|deleted|restricted|unavailable`)이므로 `= 'ok'` 는
    allowlist 이며, 스키마에 상태가 추가돼도 자동으로 공개되지 않는다(fail-closed).
    내부(owner)·계보 경로는 이 조건을 받지 않으므로 tombstone 이력은 그대로 열람된다.
    """
    if not _check_visibility(visibility):
        return []
    return [
        f"{document_alias}.is_private = false",
        public_scope_clause(source_alias),
        f"{source_alias}.publish_summary <> 'denied'",
        f"{document_alias}.access_state = 'ok'",
    ]


def make_source_id(source_kind: str, source_owner: str, source_native_id: str) -> str:
    """계약 §5: `source_id = (source_kind, source_owner, source_native_id)` 의 결정적 해시."""
    payload = "\x1f".join(
        [str(source_kind).strip(), str(source_owner).strip(), str(source_native_id).strip()]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def make_document_id(source_id: str, external_id: str) -> str:
    """계약 §5: `document_id = (source_id, external_id)` 의 결정적 해시."""
    payload = "\x1f".join([str(source_id).strip(), str(external_id).strip()])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ResearchRepository:
    """신설 테이블에만 쓰는 저장소. 기존 테이블은 읽지도 쓰지도 않는다."""

    def __init__(self, conn, *, schema: str | None = None) -> None:
        self.conn = conn
        self.schema = schema

    # ------------------------------------------------------------------ helpers
    def _t(self, name: str) -> str:
        return qualify(self.schema, name)

    def _one(self, sql: str, params: tuple = ()) -> Any:
        return self.conn.execute(sql, params).fetchone()

    def _all(self, sql: str, params: tuple = ()) -> list:
        return self.conn.execute(sql, params).fetchall()

    # ------------------------------------------------------------------ sources
    def ensure_source(
        self,
        *,
        source_kind: str,
        source_owner: str,
        source_native_id: str,
        display_name: str | None = None,
        scope: str = "public_channel",
        coverage: str = "denominator_unknown",
        requested_from: datetime | None = None,
        oldest_available: datetime | None = None,
    ) -> str:
        """소스 등록(재실행 무해). `source_id` 는 결정적이므로 두 번 불러도 같은 값이다."""
        source_id = make_source_id(source_kind, source_owner, source_native_id)
        table = self._t("research_sources")
        row = self._one(
            f"""
INSERT INTO {table} (source_id, source_kind, source_owner, source_native_id, display_name,
                     scope, coverage, requested_from, oldest_available)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (source_id) DO UPDATE
   SET display_name = COALESCE(EXCLUDED.display_name, {table}.display_name),
       scope = EXCLUDED.scope,
       updated_at = now()
RETURNING source_id
""",
            (
                source_id,
                source_kind,
                source_owner,
                str(source_native_id),
                display_name,
                scope,
                coverage,
                requested_from,
                oldest_available,
            ),
        )
        return row["source_id"]

    def get_source(self, source_id: str) -> dict | None:
        row = self._one(
            f"SELECT * FROM {self._t('research_sources')} WHERE source_id = %s", (source_id,)
        )
        return dict(row) if row is not None else None

    def list_sources(self, *, visibility: str = PUBLIC_VISIBILITY) -> list[dict]:
        clauses = source_visibility_clauses(visibility)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._all(
            f"SELECT * FROM {self._t('research_sources')}{where} ORDER BY source_id"
        )
        return [dict(row) for row in rows]

    def increment_source_progress(
        self,
        source_id: str,
        *,
        listed: int = 0,
        stored: int = 0,
        skipped_duplicate: int = 0,
        skipped_deleted: int = 0,
        skipped_restricted: int = 0,
        skipped_error: int = 0,
        skipped_no_store_policy: int = 0,
        page_cursor: str | None = None,
        last_collected_at: datetime | None = None,
        last_success_at: datetime | None = None,
        oldest_available: datetime | None = None,
        coverage: str | None = None,
    ) -> None:
        """소스 진행 상황을 **누적** 반영한다(계약 §6-1).

        `listed_count`/`stored_count`/`skipped_*` 는 소스 누적이다 — 실행별 값은
        `ingestion_runs` 행에 따로 남으므로 두 계층을 섞지 않는다. 커서·watermark·coverage 만
        마지막 실행 값으로 덮는다(커서는 전진만 한다).

        `skipped_deleted`/`skipped_restricted` 는 AGI-03(삭제·권한 처리)에서 추가됐다.
        접근 불가(429/5xx)는 전용 컬럼이 없으므로 `skipped_error` 에 합산한다.
        `skipped_no_store_policy`(migration 0004, 2026-10-14)는 `store_original='no'`
        정책 스킵 — **콘텐츠 중복이 아니다**, `skipped_duplicate` 에 섞지 않는다.
        """
        if coverage is not None and coverage not in ("known", "denominator_unknown"):
            raise ValueError(f"invalid coverage: {coverage!r}")
        assignments = [
            "listed_count = listed_count + %s",
            "stored_count = stored_count + %s",
            "skipped_duplicate = skipped_duplicate + %s",
            "skipped_deleted = skipped_deleted + %s",
            "skipped_restricted = skipped_restricted + %s",
            "skipped_error = skipped_error + %s",
            "skipped_no_store_policy = skipped_no_store_policy + %s",
            "updated_at = now()",
        ]
        params: list[Any] = [
            int(listed),
            int(stored),
            int(skipped_duplicate),
            int(skipped_deleted),
            int(skipped_restricted),
            int(skipped_error),
            int(skipped_no_store_policy),
        ]
        for column, value in (
            ("page_cursor", page_cursor),
            ("last_collected_at", last_collected_at),
            ("last_success_at", last_success_at),
            ("oldest_available", oldest_available),
            ("coverage", coverage),
        ):
            if value is None:
                continue
            assignments.append(f"{column} = %s")
            params.append(value)
        params.append(source_id)
        self.conn.execute(
            f"UPDATE {self._t('research_sources')} SET {', '.join(assignments)} "
            f"WHERE source_id = %s",
            tuple(params),
        )

    # ------------------------------------------------------------------ runs
    def begin_run(
        self,
        *,
        source_id: str,
        run_kind: str = "incremental",
        cursor_start: str | None = None,
        requested_from: datetime | None = None,
        requested_to: datetime | None = None,
        host: str | None = None,
        run_params: str | None = None,
        duplicate_of_run_id: int | None = None,
    ) -> int:
        """실행 원장 1행 — 재실행은 **새 run_id** 다(계약 §5-2)."""
        row = self._one(
            f"""
INSERT INTO {self._t('ingestion_runs')} (source_id, run_kind, status, cursor_start,
    requested_from, requested_to, host, run_params, duplicate_of_run_id)
VALUES (%s, %s, 'running', %s, %s, %s, %s, %s, %s)
RETURNING run_id
""",
            (
                source_id,
                run_kind,
                cursor_start,
                requested_from,
                requested_to,
                host,
                run_params,
                duplicate_of_run_id,
            ),
        )
        return int(row["run_id"])

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        cursor_end: str | None = None,
        listed_count: int = 0,
        stored_count: int = 0,
        skipped_duplicate: int = 0,
        skipped_deleted: int = 0,
        skipped_restricted: int = 0,
        skipped_error: int = 0,
        skipped_no_store_policy: int = 0,
        error: str | None = None,
    ) -> None:
        """같은 run 행의 상태만 확정한다(행 삭제·재생성 없음).

        `skipped_no_store_policy`(migration 0004) — `store_original='no'` 정책 스킵,
        `skipped_duplicate` 와 다른 원인이므로 별도 컬럼에 쌓는다.
        """
        self.conn.execute(
            f"""
UPDATE {self._t('ingestion_runs')}
   SET status = %s, finished_at = now(), cursor_end = %s, listed_count = %s,
       stored_count = %s, skipped_duplicate = %s, skipped_deleted = %s,
       skipped_restricted = %s, skipped_error = %s, skipped_no_store_policy = %s, error = %s
 WHERE run_id = %s
""",
            (
                status,
                cursor_end,
                listed_count,
                stored_count,
                skipped_duplicate,
                skipped_deleted,
                skipped_restricted,
                skipped_error,
                skipped_no_store_policy,
                error,
                run_id,
            ),
        )

    def get_run(self, run_id: int) -> dict | None:
        row = self._one(f"SELECT * FROM {self._t('ingestion_runs')} WHERE run_id = %s", (run_id,))
        return dict(row) if row is not None else None

    def list_runs(self, *, source_id: str | None = None, limit: int = 50) -> list[dict]:
        clauses = []
        params: list[Any] = []
        if source_id:
            clauses.append("source_id = %s")
            params.append(source_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        rows = self._all(
            f"SELECT * FROM {self._t('ingestion_runs')}{where} ORDER BY run_id DESC LIMIT %s",
            tuple(params),
        )
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------ documents
    def store_document(
        self,
        *,
        source_id: str,
        external_id: str,
        published_at: datetime | None,
        published_at_source: str,
        first_seen_at: datetime,
        canonical_uri: str | None = None,
        title: str | None = None,
        language: str | None = None,
        is_private: bool = False,
        media_refs: str = "",
        first_run_id: int | None = None,
    ) -> tuple[str, bool]:
        """문서 1건 upsert. `(document_id, inserted)` 를 돌려준다.

        `inserted` 판정은 PG 의 `xmax = 0` 규칙(INSERT 로 만들어진 행은 xmax 가 0)이며
        테스트가 이 규칙 자체를 고정한다.

        재방문 시 갱신 범위(AGI-03): `media_refs` 만 **비어 있지 않은 새 관측값으로** 갱신한다.
        선언된 미디어 참조는 revision 해시에 들어가지 않으므로(첨부 바이트 미보관) 미디어만
        바뀐 글은 새 revision 을 만들지 않는다 — 그 변화가 기록될 자리는 이 컬럼뿐이다.
        `is_private` 는 **갱신하지 않는다**: 비공개 전환은 `access_state` 전이(tombstone)로
        표현하고, 한 번 비공개로 판정된 글을 조용히 공개로 되돌리지 않는다.
        """
        document_id = make_document_id(source_id, external_id)
        doc_table = self._t("research_documents")
        row = self._one(
            f"""
INSERT INTO {doc_table} (document_id, source_id, external_id, canonical_uri,
    title, language, published_at, published_at_source, first_seen_at, is_private, media_refs,
    first_run_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (source_id, external_id) DO UPDATE SET updated_at = now(),
    media_refs = CASE WHEN EXCLUDED.media_refs <> '' THEN EXCLUDED.media_refs
                      ELSE {doc_table}.media_refs END
RETURNING document_id, (xmax = 0) AS inserted
""",
            (
                document_id,
                source_id,
                str(external_id),
                canonical_uri,
                title,
                language,
                published_at,
                published_at_source,
                first_seen_at,
                bool(is_private),
                media_refs,
                first_run_id,
            ),
        )
        return row["document_id"], bool(row["inserted"])

    def store_revision(
        self,
        *,
        document_id: str,
        revision_hash: str,
        text_digest: str,
        change_kind: str,
        collected_at: datetime,
        body_text: str | None = None,
        body_uri: str | None = None,
        attachments_canonical: str = "",
        attachment_count: int = 0,
        quality: str = "raw",
        pit_eligible: bool | None = None,
        gate_failures: str | None = None,
        run_id: int | None = None,
    ) -> tuple[int, bool]:
        """revision 1건 저장. 이미 있는 `(document_id, revision_hash)` 면 추가 0.

        `(revision_no, created)` 를 돌려준다. 문서의 현재 포인터는 **항상** 이 호출이 넘긴
        revision 을 가리키도록 갱신한다 — 새 revision 이든, 과거 내용과 다시 같아진
        기존 revision(A→B→A)이든 같다. 호출자가 이 함수를 부르는 시점이 '지금 관측된
        최신 내용'이라는 사실 자체이기 때문이다(2026-10-13 외부 검토 R2: 콘텐츠 중복
        제거는 새 행을 만들지 않는다는 뜻일 뿐, '지금 보이는 내용'과는 별개다 — 전에는
        ON CONFLICT 로 기존 revision 을 다시 만난 경우 포인터를 그대로 둬서 A→B→A 재수집
        후에도 현재 포인터가 B 를 가리키는 결함이 있었다).
        """
        table = self._t("research_document_revisions")
        # R1(2026-10-13 외부 검토) — 공통 저장 계층의 우회 방지 검사. 호출자(블로그 어댑터·
        # 텔레그램 수집 서비스 등)가 정책 확인을 빠뜨려도 여기서 한 번 더 막는다. **항상**
        # 조회한다(2026-10-13 2차 외부 검토 P1): `store_original='no'` 는 revision 행 자체를
        # 만들지 않는다는 뜻이고, 이는 body_text 가 None 이어도 성립하는 금지다 — 이전엔
        # body_text 가 있을 때만 조회해서, body_text=None 으로 부르면 'no' 소스에도 조용히
        # revision 행이 생겼다(어댑터 계층의 `should_create_revision_for_policy` 를 안 거친
        # 호출만 통과하면 우회됐다).
        policy = self._one(
            f"SELECT s.store_original FROM {self._t('research_documents')} d "
            f"JOIN {self._t('research_sources')} s ON s.source_id = d.source_id "
            f"WHERE d.document_id = %s",
            (document_id,),
        )
        if policy is None:
            raise KeyError(f"unknown document_id: {document_id}")
        store_original = policy["store_original"]
        if store_original == STORE_ORIGINAL_NONE:
            raise ValueError(
                "store_revision refused: source store_original='no' permits no revision row "
                "at all (계약 §7 권한 3분리 — 우회 방지, 2026-10-13 2차 외부 검토 P1)"
            )
        if body_text is not None and store_original != STORE_ORIGINAL_FULL:
            raise ValueError(
                f"store_revision refused: source store_original={store_original!r} does not "
                "permit storing body_text (계약 §7 권한 3분리 — 우회 방지, 2026-10-13 외부 검토 R1)"
            )
        row = self._one(
            f"""
INSERT INTO {table} (document_id, revision_no, revision_hash, text_digest, body_text, body_uri,
    attachments_canonical, attachment_count, change_kind, quality, pit_eligible, gate_failures,
    run_id, collected_at)
SELECT %s,
       COALESCE((SELECT MAX(revision_no) FROM {table} WHERE document_id = %s), 0) + 1,
       %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
ON CONFLICT (document_id, revision_hash) DO NOTHING
RETURNING revision_no
""",
            (
                document_id,
                document_id,
                revision_hash,
                text_digest,
                body_text,
                body_uri,
                attachments_canonical,
                attachment_count,
                change_kind,
                quality,
                pit_eligible,
                gate_failures,
                run_id,
                collected_at,
            ),
        )
        if row is None:
            existing = self._one(
                f"SELECT revision_no FROM {table} WHERE document_id = %s AND revision_hash = %s",
                (document_id, revision_hash),
            )
            if existing is None:  # pragma: no cover - ON CONFLICT DO NOTHING 이면 발생 불가
                raise RuntimeError("revision upsert returned nothing and no existing row")
            revision_no = int(existing["revision_no"])
            self._mark_revision_observed(document_id, revision_no, revision_hash, run_id)
            return revision_no, False

        revision_no = int(row["revision_no"])
        self._mark_revision_observed(document_id, revision_no, revision_hash, run_id)
        return revision_no, True

    def _set_current_revision(self, document_id: str, revision_no: int, revision_hash: str) -> None:
        self.conn.execute(
            f"UPDATE {self._t('research_documents')} "
            f"SET current_revision_no = %s, current_revision_hash = %s, updated_at = now() "
            f"WHERE document_id = %s",
            (revision_no, revision_hash, document_id),
        )

    def _mark_revision_observed(
        self, document_id: str, revision_no: int, revision_hash: str, run_id: int | None
    ) -> None:
        """현재 포인터 갱신(R2) + **이 revision 이 지금 다시 관측됐다**는 사실을 남긴다.

        (2026-10-13 2차 외부 검토 P2) `collected_at`/`created_at` 은 이 콘텐츠를 **최초로**
        본 시각에 멈춰 있다 — A→B→A 복귀로 기존 행을 다시 만나도 그 값은 바꾸지 않는다
        (최초 관측 시점은 그대로 PIT 근거로 남아야 한다). 대신 `last_observed_at`/
        `last_observed_run_id` 를 매번 갱신해, 독립된 run 이 같은 콘텐츠를 다시 확인한
        사실 자체를 보존한다. migration 0002 가 아직 적용되지 않은 스키마(컬럼 없음)에서는
        이 갱신만 건너뛴다 — 포인터 갱신(R2 의 핵심)은 그 컬럼과 무관하게 항상 일어난다.
        """
        self._set_current_revision(document_id, revision_no, revision_hash)
        if self._has_revision_observation_columns():
            self.conn.execute(
                f"UPDATE {self._t('research_document_revisions')} "
                f"SET last_observed_at = now(), last_observed_run_id = %s "
                f"WHERE document_id = %s AND revision_no = %s",
                (run_id, document_id, revision_no),
            )

    def _has_revision_observation_columns(self) -> bool:
        cached = getattr(self, "_observation_cols_cache", None)
        if cached is not None:
            return cached
        row = self._one(
            "SELECT 1 FROM information_schema.columns WHERE table_schema = %s "
            "AND table_name = 'research_document_revisions' AND column_name = 'last_observed_at'",
            (self.schema or "public",),
        )
        self._observation_cols_cache = row is not None
        return self._observation_cols_cache

    def mark_access_state(
        self,
        document_id: str,
        *,
        access_state: str,
        tombstoned_at: datetime | None = None,
        reason: str | None = None,
    ) -> bool:
        """접근 상태 전이(계약 §5-2) — **행을 지우지 않는다**.

        `access_state ∈ {ok, deleted, restricted, unavailable}`. 권한 오류를 삭제로
        기록하지 못하도록 호출자가 상태를 명시한다. revision 은 그대로 보존된다.
        """
        if access_state not in ("ok", "deleted", "restricted", "unavailable"):
            raise ValueError(f"invalid access_state: {access_state!r}")
        if access_state != "ok" and tombstoned_at is None:
            # 관측시각은 항상 남긴다(계약 §5-2: tombstone = 삭제 사유 + 관측시각).
            from agi.timeutil import now_utc

            tombstoned_at = now_utc()
        row = self._one(
            f"""
UPDATE {self._t('research_documents')}
   SET access_state = %s, tombstoned_at = %s, tombstone_reason = %s, updated_at = now()
 WHERE document_id = %s
RETURNING document_id
""",
            (
                access_state,
                tombstoned_at if access_state != "ok" else None,
                reason if access_state != "ok" else None,
                document_id,
            ),
        )
        return row is not None

    def current_revision(self, document_id: str) -> dict | None:
        row = self._one(
            f"""
SELECT r.* FROM {self._t('research_document_revisions')} r
  JOIN {self._t('research_documents')} d
    ON d.document_id = r.document_id AND d.current_revision_no = r.revision_no
 WHERE d.document_id = %s
""",
            (document_id,),
        )
        return dict(row) if row is not None else None

    def list_revisions(self, document_id: str) -> list[dict]:
        rows = self._all(
            f"SELECT * FROM {self._t('research_document_revisions')} "
            f"WHERE document_id = %s ORDER BY revision_no",
            (document_id,),
        )
        return [dict(row) for row in rows]

    def get_document(self, document_id: str, *, visibility: str = PUBLIC_VISIBILITY) -> dict | None:
        clauses = ["d.document_id = %s"] + document_visibility_clauses(visibility)
        params: list[Any] = [document_id]
        row = self._one(
            f"{self._document_select()} WHERE {' AND '.join(clauses)}",
            tuple(params),
        )
        return dict(row) if row is not None else None

    def list_documents(
        self,
        *,
        source_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
        visibility: str = PUBLIC_VISIBILITY,
    ) -> list[dict]:
        """무부작용 조회 — SELECT 만 한다(계약 §9-2).

        `visibility='public'`(기본)이면 개인 대화와 `publish_summary='denied'` 근거를
        **SQL 에서** 배제한다. 인증된 연구 화면만 `visibility='internal'` 을 쓴다.
        """
        clauses = document_visibility_clauses(visibility)
        params: list[Any] = []
        if source_id:
            clauses.append("d.source_id = %s")
            params.append(source_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.extend([max(1, min(int(limit), 500)), max(0, int(offset))])
        rows = self._all(
            f"{self._document_select()}{where} "
            f"ORDER BY d.published_at DESC NULLS LAST, d.document_id DESC LIMIT %s OFFSET %s",
            tuple(params),
        )
        return [dict(row) for row in rows]

    def list_document_refs(self, source_id: str) -> list[dict]:
        """소스의 문서 식별자·상태 목록 — **삭제 스윕(AGI-03)** 전용이다.

        노출 필터(`document_visibility_clauses`)를 적용하지 않는다: 스윕은 "지금 살아 있는데
        목록에 없다" 를 찾는 작업이라 tombstone 된 행도 함께 봐야 재판정이 멱등해진다.
        이 결과를 그대로 외부에 노출하지 않는다(호출자는 어댑터 내부뿐이다).
        """
        rows = self._all(
            f"SELECT document_id, external_id, access_state FROM {self._t('research_documents')} "
            f"WHERE source_id = %s ORDER BY external_id",
            (source_id,),
        )
        return [dict(row) for row in rows]

    def _document_select(self) -> str:
        doc = self._t("research_documents")
        src = self._t("research_sources")
        rev = self._t("research_document_revisions")
        return f"""
SELECT d.document_id, d.source_id, d.external_id, d.canonical_uri, d.title, d.language,
       d.published_at, d.published_at_source, d.first_seen_at, d.stored_at, d.access_state,
       d.tombstoned_at, d.tombstone_reason, d.current_revision_no, d.current_revision_hash,
       d.is_private, d.media_refs,
       s.source_kind, s.source_owner, s.source_native_id, s.display_name, s.coverage,
       s.store_original, s.external_model_send, s.publish_summary, s.scope AS source_scope,
       r.quality AS revision_quality, r.pit_eligible, r.change_kind, r.attachment_count,
       r.collected_at AS revision_collected_at, r.body_uri
  FROM {doc} d
  JOIN {src} s ON s.source_id = d.source_id
  LEFT JOIN {rev} r
    ON r.document_id = d.document_id AND r.revision_no = d.current_revision_no
"""

    def source_watermarks(self, *, visibility: str = PUBLIC_VISIBILITY) -> list[dict]:
        return [
            {
                "source_id": source["source_id"],
                "source_kind": source["source_kind"],
                "display_name": source.get("display_name"),
                "scope": source.get("scope"),
                "coverage": source["coverage"],
                "watermark": source.get("last_success_at"),
                "page_cursor": source.get("page_cursor"),
                "stored_count": source.get("stored_count"),
                "listed_count": source.get("listed_count"),
            }
            for source in self.list_sources(visibility=visibility)
        ]

    # ------------------------------------------------------------------ 지문
    def snapshot_fingerprint(self) -> dict:
        """조회 무부작용 검증용 지문(행수 + 최대 ID). GET 전후가 같아야 한다."""
        row = self._one(
            f"""
SELECT (SELECT COUNT(*) FROM {self._t('research_sources')})            AS sources,
       (SELECT COUNT(*) FROM {self._t('ingestion_runs')})              AS runs,
       (SELECT COUNT(*) FROM {self._t('research_documents')})          AS documents,
       (SELECT COUNT(*) FROM {self._t('research_document_revisions')}) AS revisions,
       (SELECT COALESCE(MAX(revision_id), 0) FROM {self._t('research_document_revisions')})
                                                                       AS max_revision_id,
       (SELECT COALESCE(MAX(run_id), 0) FROM {self._t('ingestion_runs')}) AS max_run_id
"""
        )
        return {key: row[key] for key in row.keys()}
