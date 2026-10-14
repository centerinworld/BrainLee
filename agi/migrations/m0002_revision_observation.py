"""migration 0002 — `research_document_revisions.last_observed_at` / `last_observed_run_id`
(2026-10-13 2차 외부 검토 P2: A→B→A 복귀의 마지막 관측 이력).

왜 필요한가
-----------
`research_document_revisions` 의 `(document_id, revision_hash)` 는 **콘텐츠** 중복 제거키다
(계약 §5-1). 과거 내용으로 되돌아오면(A→B→A) 그 기존 행을 다시 만나고, 새 행을 만들지
않는다 — 이것은 올바른 동작이다(내용은 그대로이므로). 하지만 그 행의 `collected_at`/
`created_at` 은 **최초로** 그 내용을 본 시각에 멈춰 있고, "이 내용이 지금 다시 관측됐다"는
사실이 어디에도 남지 않는다. revision 1(A) 이 run #1 에만 연결된 것처럼 보이면, run #3 에서
독립적으로 같은 내용이 재확인됐다는 사실(점검·PIT 재현의 근거)을 잃는다.

이 migration 은 그 사실만 1급 컬럼 2개로 남긴다 — `agi/repository.py` 의
`ResearchRepository.store_revision()` 이 그 revision 을 다시 만날 때마다(새 행이든, 콘텐츠
중복이든) 갱신한다. `collected_at`/`created_at`(최초 관측)은 그대로 둔다 — 이 migration 은
아무 기존 값도 바꾸지 않는다(둘 다 NULL 허용, 과거 행은 NULL = "재관측 기록 없음").

설계 규칙(m0001 과 동일 — 계약 AGI-01 §11)
-----------------------------------------
* up/down 을 같은 단위로 제출. down 은 이 migration 이 추가한 두 컬럼만 지운다(테이블·행 삭제 없음).
* 멱등: `ADD COLUMN IF NOT EXISTS`/`DROP COLUMN IF EXISTS`, 원장(`agi_schema_migrations`)의
  checksum 으로 드리프트를 거부한다(러너가 자동 처리, m0001 과 동일 메커니즘).
* 데이터 문장 없음(INSERT/UPDATE/DELETE 로 과거 행을 채우지 않는다) — 과거 행의 재관측
  시각은 추정하지 않는다. 적용 이후 관측부터 이 컬럼이 채워진다.
"""

from __future__ import annotations

import hashlib

from agi.db import quote_schema

VERSION = "0002_revision_observation"
NAME = "research_document_revisions: last_observed_at / last_observed_run_id"
DESCRIPTION = (
    "콘텐츠 중복 제거로 재사용되는 기존 revision 행이 다시 관측된 시각·run 을 남긴다. "
    "과거 행은 백필하지 않는다(계약 AGI-01 §11-1)."
)

TABLES = ("research_document_revisions",)

_ALTER_STATEMENTS = (
    "ALTER TABLE {p}.research_document_revisions "
    "ADD COLUMN IF NOT EXISTS last_observed_at TIMESTAMPTZ",
    "ALTER TABLE {p}.research_document_revisions "
    "ADD COLUMN IF NOT EXISTS last_observed_run_id BIGINT REFERENCES {p}.ingestion_runs(run_id)",
)

_DOWN_STATEMENTS = (
    "ALTER TABLE {p}.research_document_revisions DROP COLUMN IF EXISTS last_observed_run_id",
    "ALTER TABLE {p}.research_document_revisions DROP COLUMN IF EXISTS last_observed_at",
)


def up_statements(schema: str | None = None) -> list[str]:
    prefix = quote_schema(schema)
    return [statement.format(p=prefix) for statement in _ALTER_STATEMENTS]


def down_statements(schema: str | None = None) -> list[str]:
    prefix = quote_schema(schema)
    statements = [statement.format(p=prefix) for statement in _DOWN_STATEMENTS]
    statements.append(f"DELETE FROM {prefix}.agi_schema_migrations WHERE version = '{VERSION}'")
    return statements


def checksum(schema: str | None = None) -> str:
    """up DDL 텍스트의 sha256 — 스키마 한정자와 무관(m0001 과 같은 방식)."""
    payload = "\n--\n".join(up_statements(None))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class MigrationSpec:
    __slots__ = ("version", "name", "description", "tables")

    def __init__(self, version: str, name: str, description: str, tables: tuple[str, ...]) -> None:
        self.version = version
        self.name = name
        self.description = description
        self.tables = tables

    def up(self, schema: str | None = None) -> list[str]:
        return up_statements(schema)

    def down(self, schema: str | None = None) -> list[str]:
        return down_statements(schema)

    def checksum(self, schema: str | None = None) -> str:
        return checksum(schema)

    def __repr__(self) -> str:  # pragma: no cover - 진단용
        return f"<MigrationSpec {self.version} tables={len(self.tables)}>"


MIGRATION = MigrationSpec(VERSION, NAME, DESCRIPTION, TABLES)
