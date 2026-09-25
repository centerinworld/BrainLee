"""`PostgresCompatConnection.total_changes` — 결함 C 회귀 방지.

결함 C (2026-09-25 확정, planner 우선순위 P2):
  PG 호환 커넥션에 `total_changes` 가 없어
  `AttributeError: 'PostgresCompatConnection' object has no attribute 'total_changes'`
  로 죽는 호출부가 18곳이다(실측 로그 8건: `퀀트지표트리거` 7 + `카페시그널` 1 →
  "기존 시계열 브리지 갱신"이 매번 중단).

  ⚠️ 구현 제약: sqlite3 의 `total_changes` 는 **커넥션 수명 누적** 카운터이고, 호출부
  최소 4곳이 `before = conn.total_changes … conn.total_changes - before` **차분 패턴**이다
  (`scripts/ops/sync_cafe_existing_series_bridges.py` 3곳,
  `scripts/backfill_naver_ohlcv_2015_2018.py`, `scripts/qa_dart_report_item_mapping.py`,
  `scripts/backfill_stock_base_info_changes.py`). 커서 `rowcount` 로 대충 채우면 지금의
  시끄러운 AttributeError 가 **조용히 틀린 건수**로 바뀐다(더 나쁜 회귀) — 그래서
  DML 만 누적 집계하고 SELECT 는 세지 않는다.

  라이브 검증은 **TEMP TABLE + 롤백** 으로 한다(영구 쓰기 0).
"""
from __future__ import annotations

import unittest

from db_compat import is_dml_statement


class DmlClassificationTests(unittest.TestCase):
    def test_dml_statements(self) -> None:
        for sql in (
            "INSERT INTO t(a) VALUES (1)",
            "  insert into t(a) values (1)",
            "UPDATE t SET a=1",
            "DELETE FROM t",
            "REPLACE INTO t(a) VALUES (1)",
            "\n-- 주석\nINSERT INTO t(a) VALUES (1)",
            "/* c */ UPDATE t SET a=1",
            "WITH x AS (SELECT 1) INSERT INTO t(a) SELECT * FROM x",
        ):
            self.assertTrue(is_dml_statement(sql), sql)

    def test_select_and_ddl_are_not_dml(self) -> None:
        for sql in (
            "SELECT count(*) FROM t",
            " WITH x AS (SELECT 1) SELECT * FROM x",
            "CREATE TABLE t(a int)",
            "PRAGMA table_info(t)",
            "BEGIN",
            "",
        ):
            self.assertFalse(is_dml_statement(sql), sql)


class ReturningPathOrderingTests(unittest.TestCase):
    """`INSERT … RETURNING id` 경로의 rowcount 읽는 **순서** 를 고정한다.

    id 컬럼이 있는 테이블은 compat 가 SAVEPOINT 를 열고 `… RETURNING id` 로 실행한다.
    이때 같은 커서로 `RELEASE SAVEPOINT` 를 실행하므로, rowcount 를 release 뒤에 읽으면
    결과가 덮여 **-1** 이 되고 누적 카운터가 조용히 0 이 된다(실측: 16행 insert → 이후 -1).
    """

    class _FakeRawCursor:
        def __init__(self) -> None:
            self.statements: list[str] = []
            self._rowcount = -1
            self.connection = self

        def cursor(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=None):
            self.statements.append(sql)
            upper = sql.strip().upper()
            # DML 은 결과 행 수를 주고, SAVEPOINT/RELEASE/SELECT 는 결과를 덮어 -1 을 준다.
            self._rowcount = 1 if upper.startswith(("INSERT", "UPDATE", "DELETE")) else -1
            return self

        @property
        def rowcount(self):
            return self._rowcount

        def fetchone(self):
            return (1,)

        def fetchall(self):
            return [(1,)]

        def close(self):
            pass

    def test_returning_path_increments_counter(self) -> None:
        from db_compat import PostgresCompatCursor

        counter = {"rows": 0}
        raw = self._FakeRawCursor()
        cursor = PostgresCompatCursor(raw, {}, counter)
        cursor.execute("INSERT INTO demo_table(a) VALUES (1)")
        self.assertIn(
            "RETURNING ID",
            " ".join(raw.statements).upper(),
            "id 컬럼 경로(RETURNING id)를 타지 않아 이 테스트가 무의미해졌다",
        )
        self.assertEqual(counter["rows"], 1)

    def test_rowcount_after_release_would_be_minus_one(self) -> None:
        """release 뒤에 읽는 구현으로 되돌아가면 0 이 된다는 사실을 고정한다."""
        raw = self._FakeRawCursor()
        raw.execute("RELEASE SAVEPOINT lastrowid_probe")
        self.assertEqual(raw.rowcount, -1)


class LiveTotalChangesTests(unittest.TestCase):
    """라이브 PG — 커넥션 수명 누적 semantics 를 실제 실행으로 확인하고 롤백한다."""

    def test_cumulative_counts_dml_only_and_rolls_back(self) -> None:
        from db_compat import connect_primary_db

        try:
            conn = connect_primary_db(timeout=15)
        except Exception as exc:  # pragma: no cover - 연결 실패는 skip
            self.skipTest(f"라이브 PostgreSQL 접속 불가: {exc}")
        try:
            self.assertEqual(conn.total_changes, 0)
            conn.execute("CREATE TEMP TABLE total_changes_probe(x int)")
            conn.execute("SELECT count(*) FROM total_changes_probe").fetchall()
            self.assertEqual(conn.total_changes, 0, "SELECT 는 세면 안 된다")

            conn.execute("INSERT INTO total_changes_probe(x) VALUES (1),(2),(3)")
            self.assertEqual(conn.total_changes, 3)

            before = conn.total_changes
            conn.execute("INSERT INTO total_changes_probe(x) SELECT x FROM total_changes_probe")
            self.assertEqual(conn.total_changes - before, 3, "차분 패턴")

            conn.execute("UPDATE total_changes_probe SET x = x + 1")
            self.assertEqual(conn.total_changes, 12)

            conn.execute("DELETE FROM total_changes_probe WHERE x > 2")
            self.assertEqual(conn.total_changes, 16)

            conn.execute("SELECT count(*) FROM total_changes_probe").fetchall()
            self.assertEqual(conn.total_changes, 16, "SELECT 뒤에도 누적값 불변")
        finally:
            conn.rollback()
            conn.close()

    def test_rollback_left_nothing_behind(self) -> None:
        from db_compat import connect_primary_db

        try:
            conn = connect_primary_db(timeout=15)
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"라이브 PostgreSQL 접속 불가: {exc}")
        try:
            exists = conn.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema LIKE 'pg_temp%' AND table_name='total_changes_probe'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(exists, 0, "TEMP TABLE 이 세션에 남아 있다(영구 쓰기 없음 확인)")


if __name__ == "__main__":
    unittest.main()
