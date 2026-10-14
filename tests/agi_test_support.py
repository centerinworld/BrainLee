"""AGI 테스트 지원 — 임시 PostgreSQL 스키마 격리와 정리 보장.

왜 임시 스키마인가
------------------
AGI 계약(AGI-01 §11-2)은 **라이브 적용을 금지**한다. 그래서 테스트는 마이그레이션을
`public` 이 아니라 일회용 스키마(`agi_slice_test_*`)에 실제로 실행한다.

  * 실제 PostgreSQL 16 에서 DDL·UNIQUE·FK·CHECK·ON CONFLICT 가 **진짜로** 동작하는지 확인한다
    (SQLite 대체물이나 mock 으로는 `ON CONFLICT (source_id, external_id)` 나
    `xmax = 0` 같은 PG 고유 동작을 검증할 수 없다).
  * 끝나면 `DROP SCHEMA … CASCADE` 로 흔적을 남기지 않고, 그 사실을 다시 조회해 확인한다.
  * `public` 은 전혀 건드리지 않는다.

연결은 프로덕션과 같은 헬퍼(`db_compat.connect_primary_db`)를 쓴다 — 테스트가 다른
커넥션 구현을 쓰면 실패를 못 잡는다.
"""

from __future__ import annotations

import contextlib
import re
import uuid

from agi.db import connect_primary
from agi.migrations import MIGRATIONS
from agi.migrations.runner import MigrationRunner

TEMP_SCHEMA_PREFIX = "agi_slice_test"

_WRITE_VERBS = ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "CREATE", "ALTER", "DROP", "GRANT")


def new_schema_name() -> str:
    return f"{TEMP_SCHEMA_PREFIX}_{uuid.uuid4().hex[:10]}"


def schema_exists(conn, schema: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s", (schema,)
        ).fetchone()
        is not None
    )


def tables_in_schema(conn, schema: str) -> list[str]:
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = %s ORDER BY table_name",
        (schema,),
    ).fetchall()
    return [row["table_name"] for row in rows]


@contextlib.contextmanager
def isolated_schema(*, migrate: bool = True, drop: bool = True):
    """일회용 스키마를 만들고(선택: migration 0001 적용) 끝나면 반드시 지운다.

    `drop=False` 는 정리 실패를 재현하는 테스트용이 아니라, 정리 여부를 호출자가
    직접 검사하려는 경우에만 쓴다(기본은 항상 정리).
    """
    schema = new_schema_name()
    conn = connect_primary(timeout=60)
    try:
        conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        conn.commit()
        if migrate:
            # 등록된 migration 전부를 순서대로 적용한다(2026-10-13: m0002 추가 이후
            # MIGRATIONS[0] 만 적용하면 이후 migration 의 컬럼이 테스트 스키마에 없다).
            for migration in MIGRATIONS:
                result = MigrationRunner(conn, schema=schema).up(migration, dry_run=False)
                assert result.status == "applied", result.as_dict()
        yield conn, schema
    finally:
        with contextlib.suppress(Exception):
            conn.rollback()
        if drop:
            with contextlib.suppress(Exception):
                conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                conn.commit()
        with contextlib.suppress(Exception):
            conn.close()


class StatementRecorder:
    """`conn.execute` 를 감싸 실행된 SQL 을 기록한다.

    "레거시 테이블은 읽기 전용" 같은 주장을 **행 수 비교가 아니라 실행된 문장**으로
    증명하기 위한 도구다(스케줄러가 동시에 행을 추가해도 판정이 흔들리지 않는다).
    """

    def __init__(self, conn) -> None:
        self._conn = conn
        self.statements: list[str] = []
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.statements.append(sql)
        self.calls.append((sql, tuple(params or ())))
        return self._conn.execute(sql, params)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)

    @staticmethod
    def _normalized(sql: str) -> str:
        return re.sub(r"\s+", " ", sql).strip().upper()

    def writes_to(self, table_names) -> list[str]:
        """주어진 테이블에 대한 쓰기(비-SELECT) 문장만 골라낸다."""
        found = []
        for sql in self.statements:
            flat = self._normalized(sql)
            for table in table_names:
                if re.search(rf"\b{re.escape(table.upper())}\b", flat) and flat.startswith(_WRITE_VERBS):
                    found.append(sql)
        return found

    def statements_touching(self, table_names) -> list[str]:
        found = []
        for sql in self.statements:
            flat = self._normalized(sql)
            if any(re.search(rf"\b{re.escape(table.upper())}\b", flat) for table in table_names):
                found.append(sql)
        return found
