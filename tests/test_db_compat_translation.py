"""Focused PostgreSQL-compatibility regression tests for the db_compat shim.

Pure string-transformation tests (no DB connection) covering the SQLite→PostgreSQL
translation rules that regressed or were missing during the cutover. These pin the
behaviour fixed in the 2026-09-19 remediation so a future change cannot silently
re-break them.
"""
from db_compat import (
    translate_sqlite_sql,
    _replace_qmark_placeholders,
    _replace_named_placeholders,
)


def test_date_start_of_month_localtime():
    # routes/trend.py:520 — 3-arg SQLite date() used to raise
    # "function date(unknown,unknown,unknown) does not exist".
    out = translate_sqlite_sql(
        "SELECT DISTINCT stock_code FROM peak_holding WHERE strategy = ? "
        "AND entry_date >= date('now', 'start of month', 'localtime')"
    )
    assert "date_trunc('month', now())" in out
    assert "TO_CHAR(" in out
    assert "date('now'" not in out


def test_is_annual_is_true_false():
    assert "is_annual::text IN ('1', 'true', 't')) IS TRUE" in translate_sqlite_sql(
        "SELECT * FROM t WHERE is_annual IS TRUE"
    )
    assert "is_annual::text IN ('0', 'false', 'f')) IS TRUE" in translate_sqlite_sql(
        "SELECT * FROM t WHERE is_annual IS FALSE"
    )
    assert "IS NOT TRUE" in translate_sqlite_sql(
        "SELECT * FROM t WHERE is_annual IS NOT TRUE"
    )


def test_datetime_now():
    assert "CURRENT_TIMESTAMP" in translate_sqlite_sql(
        "INSERT INTO t (x) VALUES (datetime('now'))"
    )


def test_glob_is_translated():
    out = translate_sqlite_sql(
        "SELECT * FROM t WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'"
    )
    assert "~" in out
    assert "GLOB" not in out


def test_json_extract_and_object():
    out = translate_sqlite_sql("SELECT json_extract(x, '$.path') FROM t")
    assert "jsonb_path_query_first" in out
    assert "(x)::jsonb" in out
    assert "json_build_object(" in translate_sqlite_sql("SELECT json_object('a', 1)")


def test_qmark_placeholder():
    assert _replace_qmark_placeholders("SELECT * FROM t WHERE x = ? AND y = ?") == (
        "SELECT * FROM t WHERE x = %s AND y = %s"
    )
    # '?' inside a string literal must be preserved.
    assert _replace_qmark_placeholders("SELECT '?' AS q WHERE x = ?") == (
        "SELECT '?' AS q WHERE x = %s"
    )


def test_named_placeholder():
    assert _replace_named_placeholders("WHERE x = :code AND y IN :codes") == (
        "WHERE x = %(code)s AND y IN %(codes)s"
    )


def test_named_placeholder_preserves_casts():
    assert _replace_named_placeholders("SELECT x::text FROM t WHERE y = :yy") == (
        "SELECT x::text FROM t WHERE y = %(yy)s"
    )
    assert _replace_named_placeholders("SELECT z = 12::int") == "SELECT z = 12::int"


def test_named_placeholder_skips_string_literals():
    assert _replace_named_placeholders("SELECT 'a:b' AS lit WHERE x = :xx") == (
        "SELECT 'a:b' AS lit WHERE x = %(xx)s"
    )


def test_insert_or_replace_stays_for_cursor_path():
    # The keyword is left for PostgresCompatCursor._execute_insert_or_replace to
    # rewrite into ON CONFLICT; translate_sqlite_sql only handles inner functions/params.
    out = translate_sqlite_sql("INSERT OR REPLACE INTO t (a) VALUES (?)")
    assert "INSERT OR REPLACE" in out
    assert "%s" in out
