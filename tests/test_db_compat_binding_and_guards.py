"""Regression tests for the PostgreSQL cutover repair package.

Covers behaviour not already asserted by ``test_db_compat_regressions.py``:
- literal percent escaping in ``execute`` AND ``executemany`` (LIKE '%b%' patterns
  must never be read as psycopg ``%b``/``%t`` placeholders),
- named mapping parameters (``:name``) including ``executemany`` dict mappings,
- the PostgreSQL ``readonly`` / ``statement_timeout`` connection contract,
- the ``is_annual IS [NOT] TRUE/FALSE`` translation compared against real SQLite.
"""
from __future__ import annotations

import sqlite3
import unittest

from db_compat import (
    _escape_literal_percent,
    _replace_named_placeholders,
    connect_primary_db,
    translate_sqlite_sql,
)


def _scalar(cursor, sql: str, params=None):
    row = cursor.execute(sql, params).fetchone()
    assert row is not None
    return row[0]


class PercentEscapingUnitTests(unittest.TestCase):
    def test_like_literal_letters_are_data_not_placeholders(self) -> None:
        self.assertEqual(_escape_literal_percent("LIKE '%b%'"), "LIKE '%%b%%'")
        self.assertEqual(_escape_literal_percent("LIKE '%s%'"), "LIKE '%%s%%'")
        self.assertEqual(_escape_literal_percent("LIKE '%t%'"), "LIKE '%%t%%'")

    def test_real_placeholders_are_preserved(self) -> None:
        self.assertEqual(_escape_literal_percent("x = %s"), "x = %s")
        self.assertEqual(_escape_literal_percent("x = %(name)s"), "x = %(name)s")

    def test_literal_percent_inside_string_is_doubled(self) -> None:
        # A SQLite literal holding two '%' chars must become four for psycopg,
        # which then hands the server the same two literal percent characters.
        self.assertEqual(_escape_literal_percent("n LIKE '50%%'"), "n LIKE '50%%%%'")

    def test_doubled_quote_inside_literal(self) -> None:
        self.assertEqual(_escape_literal_percent("x = 'a''%b'"), "x = 'a''%%b'")

    def test_named_placeholder_translation(self) -> None:
        self.assertEqual(
            _replace_named_placeholders("WHERE event_date = :event_date"),
            "WHERE event_date = %(event_date)s",
        )
        # ::casts and string literals must survive
        self.assertEqual(
            _replace_named_placeholders("SELECT x::text WHERE e = :e"),
            "SELECT x::text WHERE e = %(e)s",
        )


class ParameterBindingIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect_primary_db()

    def tearDown(self) -> None:
        self.conn.close()

    def test_execute_like_literal_with_qmark(self) -> None:
        rows = self.conn.execute(
            "SELECT 1 WHERE 'abc' LIKE '%b%' AND ? = ?", (1, 1)
        ).fetchall()
        self.assertEqual(len(rows), 1)

    def test_executemany_like_literal_with_qmark(self) -> None:
        self.conn.execute("CREATE TEMP TABLE _pct_it (v INTEGER, note TEXT) ON COMMIT PRESERVE ROWS")
        self.conn.commit()
        try:
            self.conn.executemany(
                "INSERT INTO _pct_it (v, note) VALUES (?, 'pct:%')", [(1,), (2,)]
            )
            n = _scalar(self.conn, "SELECT COUNT(*) FROM _pct_it WHERE note LIKE '%pct:%'")
            self.assertEqual(n, 2)
        finally:
            self.conn.execute("DROP TABLE IF EXISTS _pct_it")
            self.conn.commit()

    def test_named_mapping_executemany(self) -> None:
        self.conn.execute("CREATE TEMP TABLE _named_it (k TEXT, v TEXT) ON COMMIT PRESERVE ROWS")
        self.conn.commit()
        try:
            self.conn.executemany(
                "INSERT INTO _named_it (k, v) VALUES (:k, :v)",
                [{"k": "a", "v": "1"}, {"k": "b", "v": "2"}],
            )
            self.assertEqual(_scalar(self.conn, "SELECT COUNT(*) FROM _named_it"), 2)
        finally:
            self.conn.execute("DROP TABLE IF EXISTS _named_it")
            self.conn.commit()


class IsAnnualTruthTableTests(unittest.TestCase):
    """The PostgreSQL translation must reproduce SQLite's three-valued logic."""

    EXPRS = (
        "is_annual IS TRUE",
        "is_annual IS FALSE",
        "is_annual IS NOT TRUE",
        "is_annual IS NOT FALSE",
    )
    BASE = (
        "SELECT is_annual, ({e}) FROM "
        "(SELECT 1 AS is_annual UNION ALL SELECT 0 UNION ALL SELECT CAST(NULL AS INTEGER))"
    )

    @staticmethod
    def _norm(rows):
        return [(r[0], None if r[1] is None else int(bool(r[1]))) for r in rows]

    def test_matches_sqlite(self) -> None:
        conn = connect_primary_db()
        lit = sqlite3.connect(":memory:")
        try:
            for expr in self.EXPRS:
                statement = self.BASE.format(e=expr)
                sqlite_rows = self._norm(lit.execute(statement).fetchall())
                pg_rows = self._norm(conn.execute(translate_sqlite_sql(statement)).fetchall())
                self.assertEqual(sqlite_rows, pg_rows, expr)
        finally:
            lit.close()
            conn.close()


class ConnectionContractTests(unittest.TestCase):
    def test_readonly_primary(self) -> None:
        conn = connect_primary_db(readonly=True)
        try:
            self.assertEqual(_scalar(conn, "SHOW transaction_read_only"), "on")
        finally:
            conn.close()

    def test_timeout_and_writable_default(self) -> None:
        conn = connect_primary_db(timeout=0.5)
        try:
            self.assertIn(_scalar(conn, "SHOW statement_timeout"), ("500ms", "0.5s", "500"))
            self.assertEqual(_scalar(conn, "SHOW transaction_read_only"), "off")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
