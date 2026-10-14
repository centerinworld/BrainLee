"""AGI DB 접근 헬퍼 — 라이브 primary 연결과 스키마 한정 이름 생성.

연결은 반드시 `db_compat.connect_primary_db()` 를 통한다(runtime/AGENTS.md §2).
이 모듈은 **새 연결을 직접 열지 않고** 헬퍼만 제공한다.

스키마 한정(`schema`)은 두 가지 용도로만 쓴다.
  1) 실제 적용: `schema=None`(기본) → 비한정 이름 → `search_path` 의 `public`.
  2) 검증: 임시 스키마(`agi_slice_test_*`)에 같은 DDL/DML 을 그대로 돌려 **동작을 실측**하고
     끝나면 `DROP SCHEMA ... CASCADE` 로 흔적을 남기지 않는다(라이브 `public` 무변경).
"""

from __future__ import annotations

import re

SCHEMA_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def validate_schema(schema: str) -> str:
    """스키마 이름 검증 — SQL 문자열에 식별자를 끼워 넣기 전에 형식을 강제한다."""
    if not isinstance(schema, str) or not SCHEMA_PATTERN.match(schema):
        raise ValueError(f"invalid schema name: {schema!r}")
    return schema


def quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def quote_schema(schema: str | None) -> str:
    """SQL 에 끼워 넣을 스키마 접두 식별자(미지정이면 `public`)."""
    if schema is None:
        return "public"
    return quote_ident(validate_schema(schema))


def qualify(schema: str | None, table: str) -> str:
    """테이블 이름을 스키마 한정 형태로 만든다(스키마 미지정 시 비한정).

    `table` 이 빈 문자열이면 `"schema".` 접두만 돌려준다.
    """
    if schema is None:
        return table
    prefix = quote_schema(schema) + "."
    return prefix + table if table else prefix


def connect_primary(readonly: bool = False, timeout: float = 30.0):
    """라이브 PostgreSQL primary 연결(읽기 전용 플래그 지원)."""
    from db_compat import connect_primary_db

    return connect_primary_db(readonly=readonly, timeout=timeout)
