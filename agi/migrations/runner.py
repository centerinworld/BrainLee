"""AGI 신설 스키마 migration 실행 러너.

계약 AGI-01 §11 규칙을 코드로 강제한다.
  * 11-1: **rollback 없는 DDL 금지** — up/down 을 함께 제출하고, 러너는 둘 다 실행할 수 있다.
  * 11-2: 이 단계에서는 적용하지 않는다 — CLI 기본값이 `--dry-run` 이고, 실제 적용은
    `--apply` 로만 한다(사람 승인 단위).
  * 11-5: **멱등(재실행 무해)** + 실패 시 부분 적용 없음 — up/down 각각 한 트랜잭션이다.

적용 원장(`agi_schema_migrations`)에 version·checksum·applied_at 을 남기므로
재실행은 `skipped` 로 끝나고, DDL 텍스트가 바뀐 뒤 재적용을 시도하면 `MigrationDriftError`
로 **거부**한다(조용히 갈라지는 스키마를 만들지 않는다).

연결은 인자로 받는다(테스트가 임시 스키마를 쓰기 위함). 프로덕션 경로는
`agi.db.connect_primary()` 다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DEFAULT_SCHEMA = "public"


class MigrationError(RuntimeError):
    """migration 실행 계약 위반."""


class MigrationDriftError(MigrationError):
    """원장에 기록된 checksum 과 현재 DDL 텍스트가 다르다 — 재적용 거부."""


@dataclass(frozen=True)
class Stmt:
    """실행/출력 단위. dry-run 은 이 목록을 출력만 한다."""

    sql: str
    params: tuple = ()


@dataclass
class MigrationResult:
    direction: str
    version: str
    schema: str
    status: str
    statements: list[Stmt] = field(default_factory=list)
    checksum: str | None = None
    applied_at: Any = None
    reason: str | None = None
    tables: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "direction": self.direction,
            "version": self.version,
            "schema": self.schema,
            "status": self.status,
            "statement_count": len(self.statements),
            "checksum": self.checksum,
            "applied_at": str(self.applied_at) if self.applied_at is not None else None,
            "reason": self.reason,
            "tables": list(self.tables),
        }


class MigrationRunner:
    def __init__(self, conn, *, schema: str | None = DEFAULT_SCHEMA, applied_by: str | None = None):
        self.conn = conn
        self.schema = schema
        self.applied_by = applied_by or "agi-migrations"

    # ------------------------------------------------------------------ helpers
    @property
    def _prefix(self) -> str:
        from agi.db import quote_schema

        return quote_schema(self.schema)

    def _ledger(self) -> str:
        from agi.migrations.m0001_research_core import LEDGER_TABLE

        return f"{self._prefix}.{LEDGER_TABLE}"

    def ensure_schema(self) -> None:
        """스키마 자체를 보장한다(`public` 은 이미 존재 → no-op)."""
        from agi.db import validate_schema

        if self.schema is not None:
            validate_schema(self.schema)
        self.conn.execute(f"CREATE SCHEMA IF NOT EXISTS {self._prefix}")
        self.conn.commit()

    def ensure_ledger(self) -> None:
        from agi.migrations.m0001_research_core import ledger_create_statement

        self.conn.execute(ledger_create_statement(self.schema))
        self.conn.commit()

    def _table_exists(self, table: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = %s AND table_name = %s",
            (self.schema or "public", table),
        ).fetchone()
        return row is not None

    def applied_row(self, version: str) -> dict | None:
        if not self._table_exists("agi_schema_migrations"):
            return None
        row = self.conn.execute(
            f"SELECT version, name, checksum, applied_at FROM {self._ledger()} WHERE version = %s",
            (version,),
        ).fetchone()
        if row is None:
            return None
        return {
            "version": row["version"],
            "name": row["name"],
            "checksum": row["checksum"],
            "applied_at": row["applied_at"],
        }

    # ------------------------------------------------------------------ planning
    def plan_up(self, migration, *, applied_by: str | None = None) -> list[Stmt]:
        statements = [Stmt(sql) for sql in migration.up(self.schema)]
        statements.append(
            Stmt(
                f"INSERT INTO {self._ledger()} (version, name, checksum, applied_by) "
                f"VALUES (%s, %s, %s, %s)",
                (
                    migration.version,
                    migration.name,
                    migration.checksum(self.schema),
                    applied_by or self.applied_by,
                ),
            )
        )
        return statements

    def plan_down(self, migration) -> list[Stmt]:
        return [Stmt(sql) for sql in migration.down(self.schema)]

    # ------------------------------------------------------------------ execution
    def up(self, migration, *, dry_run: bool = True, applied_by: str | None = None) -> MigrationResult:
        checksum = migration.checksum(self.schema)
        plan = self.plan_up(migration, applied_by=applied_by)

        if dry_run:
            # dry-run 은 **아무것도 쓰지 않는다** — 스키마/원장 생성도 하지 않는다.
            row = self.applied_row(migration.version)
            if row is not None and row["checksum"] != checksum:
                raise MigrationDriftError(
                    f"{migration.version}: ledger checksum {row['checksum']} != current {checksum}"
                )
            return MigrationResult(
                direction="up",
                version=migration.version,
                schema=self.schema or "public",
                status="skipped" if row is not None else "dry_run",
                statements=plan,
                checksum=checksum,
                applied_at=row["applied_at"] if row is not None else None,
                reason="already_applied" if row is not None else None,
                tables=migration.tables,
            )

        self.ensure_schema()
        self.ensure_ledger()
        row = self.applied_row(migration.version)
        if row is not None:
            if row["checksum"] != checksum:
                raise MigrationDriftError(
                    f"{migration.version}: ledger checksum {row['checksum']} != current {checksum} "
                    "(DDL 텍스트가 바뀌었다 — 새 migration 을 추가하라)"
                )
            return MigrationResult(
                direction="up",
                version=migration.version,
                schema=self.schema or "public",
                status="skipped",
                statements=plan,
                checksum=checksum,
                applied_at=row["applied_at"],
                reason="already_applied",
                tables=migration.tables,
            )

        try:
            for statement in plan:
                self.conn.execute(statement.sql, statement.params)
            self.conn.commit()
        except Exception:
            # 부분 적용 금지 — 한 트랜잭션이므로 전부 되돌린다.
            self.conn.rollback()
            raise
        applied = self.applied_row(migration.version)
        return MigrationResult(
            direction="up",
            version=migration.version,
            schema=self.schema or "public",
            status="applied",
            statements=plan,
            checksum=checksum,
            applied_at=applied["applied_at"] if applied else None,
            tables=migration.tables,
        )

    def down(self, migration, *, dry_run: bool = True) -> MigrationResult:
        checksum = migration.checksum(self.schema)
        row = self.applied_row(migration.version)
        plan = self.plan_down(migration)
        if row is None:
            return MigrationResult(
                direction="down",
                version=migration.version,
                schema=self.schema or "public",
                status="skipped",
                statements=plan,
                checksum=checksum,
                reason="not_applied",
                tables=migration.tables,
            )
        if row["checksum"] != checksum:
            raise MigrationDriftError(
                f"{migration.version}: ledger checksum {row['checksum']} != current {checksum}"
            )
        if dry_run:
            return MigrationResult(
                direction="down",
                version=migration.version,
                schema=self.schema or "public",
                status="dry_run",
                statements=plan,
                checksum=checksum,
                applied_at=row["applied_at"],
                tables=migration.tables,
            )
        try:
            for statement in plan:
                self.conn.execute(statement.sql, statement.params)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return MigrationResult(
            direction="down",
            version=migration.version,
            schema=self.schema or "public",
            status="rolled_back",
            statements=plan,
            checksum=checksum,
            applied_at=row["applied_at"],
            tables=migration.tables,
        )
