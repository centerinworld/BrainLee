"""AGI migration 0001(research core) 계약 테스트.

검증 대상(계약 AGI-01 §11 DDL 규칙 · 수용기준 T10)
  * up 이 신설 4테이블 + 적용 원장을 만든다.
  * up 재실행은 **멱등**(원장에 기록이 있으면 skipped, 행 추가 0).
  * down 이 신설 테이블을 전부 제거하고 원장 행을 지운다(≠ 기존 테이블 무접촉).
  * DDL 텍스트가 바뀐 뒤 재적용 시도는 `MigrationDriftError` 로 **거부**한다.
  * 멱등키가 **DB 제약으로 실제 강제**된다(UNIQUE/FK/CHECK 위반이 진짜 실패한다).
  * 테스트가 쓰는 일회용 스키마는 정리 후 흔적이 없다(라이브 `public` 무변경).

실행:
    ./venv/bin/python -m pytest tests/test_agi_research_core_migration.py -v
"""

from __future__ import annotations

import unittest

from agi.db import connect_primary
from agi.migrations import MIGRATIONS
from agi.migrations.m0001_research_core import DOWN_TABLES, TABLES
from agi.migrations.m0003_company_reports import TABLES as M0003_TABLES
from agi.migrations.runner import MigrationDriftError, MigrationRunner
from tests.agi_test_support import (
    TEMP_SCHEMA_PREFIX,
    isolated_schema,
    schema_exists,
    tables_in_schema,
)

MIGRATION = MIGRATIONS[0]


def _sqlstate(exc: Exception) -> str | None:
    return getattr(exc, "sqlstate", None) or getattr(getattr(exc, "orig", None), "sqlstate", None)


class MigrationShapeTests(unittest.TestCase):
    """스키마 형태 — 실제 PostgreSQL 에 적용한 뒤 카탈로그로 확인한다."""

    def test_up_creates_four_tables_plus_ledger(self) -> None:
        with isolated_schema(migrate=False) as (conn, schema):
            runner = MigrationRunner(conn, schema=schema)
            result = runner.up(MIGRATION, dry_run=False)
            self.assertEqual(result.status, "applied")
            self.assertEqual(result.schema, schema)
            self.assertNotEqual(schema, "public")
            self.assertTrue(schema.startswith(TEMP_SCHEMA_PREFIX))

            created = set(tables_in_schema(conn, schema))
            for table in TABLES:
                self.assertIn(table, created, f"{table} 이 생성되지 않았다")
            self.assertIn("agi_schema_migrations", created)

    def test_real_columns_are_timestamptz_and_first_class(self) -> None:
        """계약 §11-3: 키·시간 컬럼은 1급 컬럼이어야 한다(JSON blob 단독 저장 금지)."""
        with isolated_schema() as (conn, schema):
            rows = conn.execute(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = 'research_documents'",
                (schema,),
            ).fetchall()
            types = {row["column_name"]: row["data_type"] for row in rows}
            for column in (
                "published_at",
                "first_seen_at",
                "stored_at",
                "tombstoned_at",
                "updated_at",
            ):
                self.assertEqual(types.get(column), "timestamp with time zone", column)
            for column in ("document_id", "source_id", "external_id", "published_at_source",
                           "access_state", "current_revision_hash", "media_refs"):
                self.assertIn(column, types)

            revision_types = {
                row["column_name"]: row["data_type"]
                for row in conn.execute(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = %s AND table_name = 'research_document_revisions'",
                    (schema,),
                ).fetchall()
            }
            self.assertEqual(revision_types.get("collected_at"), "timestamp with time zone")
            self.assertEqual(revision_types.get("revision_hash"), "text")
            self.assertEqual(revision_types.get("attachments_canonical"), "text")


class MigrationIdempotencyTests(unittest.TestCase):
    def test_second_up_is_skipped_without_writes(self) -> None:
        with isolated_schema() as (conn, schema):
            runner = MigrationRunner(conn, schema=schema)
            second = runner.up(MIGRATION, dry_run=False)
            self.assertEqual(second.status, "skipped")
            self.assertEqual(second.reason, "already_applied")

            ledger = conn.execute(
                f'SELECT COUNT(*) AS c FROM "{schema}".agi_schema_migrations WHERE version = %s',
                (MIGRATION.version,),
            ).fetchone()
            self.assertEqual(ledger["c"], 1)

    def test_down_removes_new_tables_and_ledger_row(self) -> None:
        with isolated_schema() as (conn, schema):
            runner = MigrationRunner(conn, schema=schema)
            # 2026-10-13: isolated_schema() 가 등록된 migration 전부(지금은 0001·0002)를
            # 적용하므로, 원장을 비우려면 **적용 역순**(0002 먼저)으로 내려야 한다 — 0001 이
            # 먼저 테이블을 지우면 0002 의 ledger 행만 남아 '여전히 적용됨'이라는 거짓 상태가 된다.
            for migration in reversed(MIGRATIONS):
                result = runner.down(migration, dry_run=False)
                self.assertEqual(result.status, "rolled_back")

            remaining = set(tables_in_schema(conn, schema))
            for table in DOWN_TABLES:
                self.assertNotIn(table, remaining, f"down 후에도 {table} 이 남았다")
            ledger_count = conn.execute(
                f'SELECT COUNT(*) AS c FROM "{schema}".agi_schema_migrations'
            ).fetchone()
            self.assertEqual(ledger_count["c"], 0)

    def test_down_then_up_returns_to_the_same_state(self) -> None:
        with isolated_schema() as (conn, schema):
            runner = MigrationRunner(conn, schema=schema)
            runner.down(MIGRATION, dry_run=False)
            again = runner.up(MIGRATION, dry_run=False)
            self.assertEqual(again.status, "applied")
            created = set(tables_in_schema(conn, schema))
            for table in TABLES:
                self.assertIn(table, created)

    def test_down_on_unapplied_schema_is_skipped(self) -> None:
        with isolated_schema(migrate=False) as (conn, schema):
            result = MigrationRunner(conn, schema=schema).down(MIGRATION, dry_run=False)
            self.assertEqual(result.status, "skipped")
            self.assertEqual(result.reason, "not_applied")


class MigrationDryRunTests(unittest.TestCase):
    """T10: down 스크립트 없는 migration 0 + 적용 없이 스키마 diff 산출."""

    def test_dry_run_writes_nothing(self) -> None:
        with isolated_schema(migrate=False) as (conn, schema):
            runner = MigrationRunner(conn, schema=schema)
            plan = runner.up(MIGRATION, dry_run=True)
            self.assertEqual(plan.status, "dry_run")
            self.assertTrue(plan.statements)
            self.assertEqual(tables_in_schema(conn, schema), [])

            rollback_plan = runner.down(MIGRATION, dry_run=True)
            self.assertEqual(rollback_plan.status, "skipped")

        # 적용된 스키마에서의 down dry-run 은 계획만 돌려주고 아무것도 지우지 않는다.
        with isolated_schema() as (conn, schema):
            runner = MigrationRunner(conn, schema=schema)
            before = set(tables_in_schema(conn, schema))
            rollback_plan = runner.down(MIGRATION, dry_run=True)
            self.assertEqual(rollback_plan.status, "dry_run")
            self.assertEqual(len(rollback_plan.statements), len(DOWN_TABLES) + 1)
            self.assertEqual(set(tables_in_schema(conn, schema)), before)

    def test_down_plan_covers_every_up_table(self) -> None:
        with isolated_schema() as (conn, schema):
            runner = MigrationRunner(conn, schema=schema)
            up_sql = " ".join(statement.sql for statement in runner.plan_up(MIGRATION))
            down_sql = " ".join(statement.sql for statement in runner.plan_down(MIGRATION))
            for table in DOWN_TABLES:
                self.assertIn(f"CREATE TABLE IF NOT EXISTS", up_sql)
                self.assertIn(f"DROP TABLE IF EXISTS", down_sql)
                self.assertIn(table, up_sql)
                self.assertIn(table, down_sql)
            self.assertIn(f"DELETE FROM", down_sql)


class MigrationDriftTests(unittest.TestCase):
    def test_changed_ddl_after_apply_is_rejected(self) -> None:
        class DriftedSpec:
            version = MIGRATION.version
            name = MIGRATION.name
            tables = MIGRATION.tables

            @staticmethod
            def up(schema=None):
                return MIGRATION.up(schema)

            @staticmethod
            def down(schema=None):
                return MIGRATION.down(schema)

            @staticmethod
            def checksum(schema=None):
                return "0" * 64

        with isolated_schema() as (conn, schema):
            runner = MigrationRunner(conn, schema=schema)
            with self.assertRaises(MigrationDriftError):
                runner.up(DriftedSpec(), dry_run=False)


class DbConstraintEnforcementTests(unittest.TestCase):
    """멱등키가 **DB 제약으로** 강제되는지 — 애플리케이션 upsert 만으로 대체하지 않는다(§11-3)."""

    def test_source_natural_key_is_unique(self) -> None:
        with isolated_schema() as (conn, schema):
            table = f'"{schema}".research_sources'
            conn.execute(
                f"INSERT INTO {table} (source_id, source_kind, source_owner, source_native_id) "
                f"VALUES (%s, %s, %s, %s)",
                ("a" * 64, "telegram_channel", "owner", "chan-1"),
            )
            conn.commit()
            with self.assertRaises(Exception) as ctx:
                conn.execute(
                    f"INSERT INTO {table} (source_id, source_kind, source_owner, source_native_id) "
                    f"VALUES (%s, %s, %s, %s)",
                    ("b" * 64, "telegram_channel", "owner", "chan-1"),
                )
            self.assertEqual(_sqlstate(ctx.exception), "23505", str(ctx.exception))

    def test_document_natural_key_is_unique(self) -> None:
        with isolated_schema() as (conn, schema):
            conn.execute(
                f'INSERT INTO "{schema}".research_sources '
                f"(source_id, source_kind, source_owner, source_native_id) VALUES (%s, %s, %s, %s)",
                ("s" * 64, "telegram_channel", "owner", "chan-1"),
            )
            conn.commit()
            docs = f'"{schema}".research_documents'
            insert = (
                f"INSERT INTO {docs} (document_id, source_id, external_id, published_at_source, "
                f"first_seen_at) VALUES (%s, %s, %s, 'source_meta', now())"
            )
            conn.execute(insert, ("d" * 64, "s" * 64, "1"))
            conn.commit()
            with self.assertRaises(Exception) as ctx:
                conn.execute(insert, ("e" * 64, "s" * 64, "1"))
            self.assertEqual(_sqlstate(ctx.exception), "23505", str(ctx.exception))

    def test_revision_hash_is_unique_per_document(self) -> None:
        with isolated_schema() as (conn, schema):
            conn.execute(
                f'INSERT INTO "{schema}".research_sources '
                f"(source_id, source_kind, source_owner, source_native_id) VALUES (%s, %s, %s, %s)",
                ("s" * 64, "telegram_channel", "owner", "chan-1"),
            )
            conn.execute(
                f'INSERT INTO "{schema}".research_documents '
                f"(document_id, source_id, external_id, published_at_source, first_seen_at) "
                f"VALUES (%s, %s, %s, 'source_meta', now())",
                ("d" * 64, "s" * 64, "1"),
            )
            conn.commit()
            revs = f'"{schema}".research_document_revisions'
            insert = (
                f"INSERT INTO {revs} (document_id, revision_no, revision_hash, text_digest, "
                f"collected_at) VALUES (%s, %s, %s, %s, now())"
            )
            conn.execute(insert, ("d" * 64, 1, "h" * 64, "t" * 64))
            conn.commit()
            with self.assertRaises(Exception) as ctx:
                conn.execute(insert, ("d" * 64, 2, "h" * 64, "t" * 64))
            self.assertEqual(_sqlstate(ctx.exception), "23505", str(ctx.exception))

    def test_foreign_key_is_enforced(self) -> None:
        with isolated_schema() as (conn, schema):
            with self.assertRaises(Exception) as ctx:
                conn.execute(
                    f'INSERT INTO "{schema}".research_documents '
                    f"(document_id, source_id, external_id, published_at_source, first_seen_at) "
                    f"VALUES (%s, %s, %s, 'source_meta', now())",
                    ("d" * 64, "missing-source", "1"),
                )
            self.assertEqual(_sqlstate(ctx.exception), "23503", str(ctx.exception))

    def test_check_constraints_reject_invalid_enums(self) -> None:
        with isolated_schema() as (conn, schema):
            with self.assertRaises(Exception) as ctx:
                conn.execute(
                    f'INSERT INTO "{schema}".research_sources '
                    f"(source_id, source_kind, source_owner, source_native_id, coverage) "
                    f"VALUES (%s, %s, %s, %s, %s)",
                    ("a" * 64, "telegram_channel", "owner", "chan-1", "complete"),
                )
            self.assertEqual(_sqlstate(ctx.exception), "23514", str(ctx.exception))

    def test_permission_defaults_are_fail_closed(self) -> None:
        """계약 §7: 소스 등록 기본값은 metadata_only / denied / denied / term."""
        with isolated_schema() as (conn, schema):
            conn.execute(
                f'INSERT INTO "{schema}".research_sources '
                f"(source_id, source_kind, source_owner, source_native_id) VALUES (%s, %s, %s, %s)",
                ("a" * 64, "telegram_channel", "owner", "chan-1"),
            )
            conn.commit()
            row = conn.execute(
                f'SELECT store_original, external_model_send, publish_summary, retention_class, '
                f'coverage FROM "{schema}".research_sources WHERE source_id = %s',
                ("a" * 64,),
            ).fetchone()
            self.assertEqual(row["store_original"], "metadata_only")
            self.assertEqual(row["external_model_send"], "denied")
            self.assertEqual(row["publish_summary"], "denied")
            self.assertEqual(row["retention_class"], "term")
            self.assertEqual(row["coverage"], "denominator_unknown")


class IsolationTests(unittest.TestCase):
    """테스트가 라이브 `public` 을 건드리지 않고, 쓰는 스키마는 정리된다."""

    def test_temp_schema_is_dropped_and_public_untouched(self) -> None:
        """임시 스키마만 쓴다 — `public` 의 AGI 테이블 유무는 **테스트가 바뀌지 않는다.**

        ⚠️ 2026-10-11 갱신: 종전엔 "`public` 에 AGI 테이블이 **없어야** 한다"고 단언했다(적용 전
        기준). Planner 승인으로 migration 0001 이 실제 적용되어 그 단언은 의미를 잃었다. 지금은
        **적용 여부와 무관하게** 성립하는 형태로 검사한다 — 테스트 전후 `public` 의 AGI 테이블
        존재 상태가 동일해야 한다(테스트는 임시 스키마에만 DDL 을 실행한다).

        `public` 전체 테이블 목록의 완전 일치를 쓰지 않는 이유: 라이브 DB 는 다른 세션·스케줄러가
        동시에 쓰는 공유 저장소라 무관한 DDL 이 끼어들면 거짓 실패가 된다(AGI 테이블 존재 상태만
        비교하면 그 문제가 없다).
        """
        watched = (*TABLES, *M0003_TABLES, "agi_schema_migrations")

        baseline = connect_primary(readonly=True, timeout=30)
        try:
            before = {name: (name in tables_in_schema(baseline, "public")) for name in watched}
        finally:
            baseline.close()

        seen_schema = None
        with isolated_schema() as (conn, schema):
            seen_schema = schema
            self.assertTrue(schema_exists(conn, schema))
            self.assertNotEqual(schema, "public")
            # 임시 스키마에는 만들어졌다(쓰기가 실제로 일어났다).
            self.assertEqual(sorted(tables_in_schema(conn, schema)), sorted(watched))
            after = {name: (name in tables_in_schema(conn, "public")) for name in watched}
            self.assertEqual(after, before, "테스트가 라이브 public 의 AGI 테이블 상태를 바꿨다")

        checker = connect_primary(timeout=30)
        try:
            self.assertIsNotNone(seen_schema)
            self.assertFalse(schema_exists(checker, seen_schema))
            self.assertEqual(
                {name: (name in tables_in_schema(checker, "public")) for name in watched},
                before,
                "테스트가 라이브 public 의 AGI 테이블 상태를 바꿨다",
            )
        finally:
            checker.close()


if __name__ == "__main__":
    unittest.main()
