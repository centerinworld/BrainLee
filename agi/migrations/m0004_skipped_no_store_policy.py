"""migration 0004 — `ingestion_runs`/`research_sources.skipped_no_store_policy`
(2026-10-14 외부 검토 반영: store_original='no' 스킵이 중복 집계에 섞이는 문제).

왜 필요한가
-----------
`store_original='no'` 소스는 애초에 revision 을 만들지 않기로 한 **정책 결정**이다(계약
§7). 이것은 "같은 내용을 다시 만나 revision 을 안 만드는" **콘텐츠 중복**(`skipped_duplicate`)
과 원인이 전혀 다르다 — 하나는 "읽어보니 똑같았다", 다른 하나는 "정책상 안 읽었다"다.
`agi/adapters/blog.py`는 이미 둘을 인메모리 카운터(`_Counters.not_stored_by_policy`)로
분리해 응답 dict의 `skipped_no_store_policy` 로 돌려주지만, **DB 원장(`ingestion_runs`/
`research_sources`)에는 전용 컬럼이 없어 그 값이 저장되지 않고 사라진다** — 실행이 끝나면
그 소스가 정책상 얼마나 건너뛰었는지 원장만 봐서는 알 수 없다. `agi/service.py`(텔레그램
경로, `_store_post`)는 더 심하다 — 전용 카운터가 전혀 없어서 정책 스킵이 그대로
`skipped_duplicate`에 섞여 들어가 **중복률이 실제보다 높게 보인다**.

이 migration 은 그 사실만 1급 컬럼 2개로 남긴다. `agi/repository.py`의
`finish_run()`/`increment_source_progress()`가 새 kwarg로 받아 누적하고,
`agi/adapters/blog.py`·`agi/service.py` 양쪽이 이제 이 값을 채워 보낸다.

설계 규칙(m0001~m0003 과 동일 — 계약 AGI-01 §11)
-----------------------------------------
* up/down 을 같은 단위로 제출. down 은 이 migration 이 추가한 두 컬럼만 지운다(테이블·행 삭제 없음).
* 멱등: `ADD COLUMN IF NOT EXISTS`/`DROP COLUMN IF EXISTS`, 원장 checksum 으로 드리프트 거부.
* 데이터 문장 없음 — 과거 실행 행의 정책 스킵 건수는 소급 추정하지 않는다(적용 이후부터 채워짐).
"""

from __future__ import annotations

import hashlib

from agi.db import quote_schema

VERSION = "0004_skipped_no_store_policy"
NAME = "ingestion_runs / research_sources: skipped_no_store_policy"
DESCRIPTION = (
    "store_original='no' 정책 스킵을 콘텐츠 중복(skipped_duplicate)과 분리해 집계한다. "
    "과거 실행 행은 백필하지 않는다(계약 AGI-01 §11-1)."
)

TABLES = ("ingestion_runs", "research_sources")

_ALTER_STATEMENTS = (
    "ALTER TABLE {p}.ingestion_runs "
    "ADD COLUMN IF NOT EXISTS skipped_no_store_policy BIGINT NOT NULL DEFAULT 0",
    "ALTER TABLE {p}.research_sources "
    "ADD COLUMN IF NOT EXISTS skipped_no_store_policy BIGINT NOT NULL DEFAULT 0",
)

_DOWN_STATEMENTS = (
    "ALTER TABLE {p}.research_sources DROP COLUMN IF EXISTS skipped_no_store_policy",
    "ALTER TABLE {p}.ingestion_runs DROP COLUMN IF EXISTS skipped_no_store_policy",
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
