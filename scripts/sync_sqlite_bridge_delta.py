#!/usr/bin/env python3
"""Upsert selected legacy SQLite tables into the PostgreSQL primary database.

This is a temporary cutover bridge for jobs that have not yet been converted to
``connect_primary_db``. It is idempotent and requires a primary or unique key.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db_utils import STOCK_DB_PATH  # noqa: E402
from scripts.migrate_operational_postgres import POSTGRES_URL, convert  # noqa: E402

DEFAULT_TABLES = ("backtest_run_specs", "backtest_runs")


def quote_sqlite(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def conflict_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """Pick the ON CONFLICT target for upserting into PostgreSQL.

    Prefer a secondary natural-key unique index (origin 'u') over a surrogate
    integer primary key ('pk' origin / rowid alias) when both exist. Tables
    written with SQLite's ``INSERT OR REPLACE`` (e.g. signal_result) regenerate
    a new surrogate id on every re-write of the same logical row, so bridging
    on the surrogate id alone lets the row's *other* unique constraint (the
    natural key) collide in PostgreSQL without ON CONFLICT catching it. Using
    the natural key as the target makes the upsert idempotent regardless of
    which id the source row currently carries.
    """
    natural_key: list[str] = []
    primary: list[str] = []
    for index in conn.execute(f"PRAGMA index_list({quote_sqlite(table)})"):
        if not index[2] or (len(index) > 4 and index[4]):
            continue  # not unique, or a partial index
        columns = [
            row[2]
            for row in conn.execute(f"PRAGMA index_info({quote_sqlite(index[1])})")
            if row[2]
        ]
        if not columns:
            continue
        origin = index[3] if len(index) > 3 else "c"
        if origin == "pk":
            primary = columns
        elif not natural_key:
            natural_key = columns
    if natural_key:
        return natural_key
    if primary:
        return primary
    info = conn.execute(f"PRAGMA table_info({quote_sqlite(table)})").fetchall()
    primary = [row[1] for row in sorted(info, key=lambda row: row[5]) if row[5]]
    if primary:
        return primary
    raise RuntimeError(f"{table}: no primary or unique key; refusing unsafe merge")


def sync_table(
    sqlite_conn: sqlite3.Connection,
    pg_conn: psycopg.Connection,
    table: str,
    chunk_size: int,
) -> int:
    columns = sqlite_conn.execute(f"PRAGMA table_info({quote_sqlite(table)})").fetchall()
    if not columns:
        raise RuntimeError(f"{table}: missing SQLite table")
    all_names = [row[1] for row in columns]
    declared = {row[1]: row[2] for row in columns}
    row_index = {name: idx for idx, name in enumerate(all_names)}
    conflicts = conflict_columns(sqlite_conn, table)

    # SQLite and PostgreSQL each autoincrement `id` independently once a table
    # is no longer bridged 1:1 by surrogate id, so the two sequences drift out
    # of correspondence. Copying SQLite's id verbatim into an INSERT ON
    # CONFLICT(<natural key>) can then collide with a PostgreSQL row that
    # already owns that id under a *different* natural key (that constraint
    # isn't the one named in ON CONFLICT, so it isn't suppressed). When the
    # conflict target is a natural key, leave id out and let PostgreSQL's own
    # IDENTITY sequence assign it.
    id_tracked = conflicts == ["id"]
    names = all_names if id_tracked or "id" not in all_names else [
        n for n in all_names if n != "id"
    ]

    updates = [name for name in names if name not in conflicts]
    statement = sql.SQL("INSERT INTO public.{} ({}) VALUES ({}) ON CONFLICT ({}) ").format(
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, names)),
        sql.SQL(", ").join(sql.Placeholder() for _ in names),
        sql.SQL(", ").join(map(sql.Identifier, conflicts)),
    )
    if updates:
        statement += sql.SQL("DO UPDATE SET {} ").format(
            sql.SQL(", ").join(
                sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(name), sql.Identifier(name))
                for name in updates
            )
        )
    else:
        statement += sql.SQL("DO NOTHING ")

    source_sql = f"SELECT * FROM {quote_sqlite(table)}"
    source_params: tuple[object, ...] = ()
    if id_tracked and "id" in all_names and "INT" in declared["id"].upper():
        # Safe here: id is copied 1:1, so PostgreSQL's own max(id) is a valid
        # high-watermark for "rows SQLite hasn't sent yet".
        high_watermark = pg_conn.execute(
            sql.SQL("SELECT MAX(id) FROM public.{}").format(sql.Identifier(table))
        ).fetchone()[0]
        if high_watermark is not None:
            source_sql += " WHERE id >= ?"
            source_params = (high_watermark,)
    # 2026-08-24: 예전엔 여기 "elif created_at in all_names" 분기로 created_at
    # 워터마크를 썼으나, cf_validation_flags/cafe_signal_mentions 실측에서 반복
    # 재발 확인 — created_at이 삽입/동기화 순서와 반드시 일치한다는 보장이 없는
    # 테이블(과거 배치 재계산이 오래된 타임스탬프를 그대로 들고 새 행을 넣는 경우
    # 등)에서는 postgres 현재 MAX(created_at)보다 오래된 신규/누락 행이 영구히
    # 스캔범위 밖으로 밀려나는 구조적 blind spot이었음. id_tracked가 아닌 모든
    # 테이블(자연키 conflict target)은 항상 전체 upsert로 스캔 — ON CONFLICT가
    # 이미 idempotent라 정확성이 증분 최적화보다 우선.
    cursor = sqlite_conn.execute(source_sql, source_params)
    total = 0
    while rows := cursor.fetchmany(chunk_size):
        values = [
            tuple(convert(row[row_index[name]], declared[name]) for name in names)
            for row in rows
        ]
        with pg_conn.cursor() as pg_cursor:
            pg_cursor.executemany(statement, values)
        pg_conn.commit()
        total += len(values)
    return total


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tables", nargs="*", default=list(DEFAULT_TABLES))
    parser.add_argument("--chunk-size", type=int, default=1000)
    args = parser.parse_args()

    sqlite_conn = sqlite3.connect(str(STOCK_DB_PATH))
    pg_conn = psycopg.connect(POSTGRES_URL)
    try:
        for table in args.tables:
            rows = sync_table(sqlite_conn, pg_conn, table, args.chunk_size)
            sqlite_count = sqlite_conn.execute(
                f"SELECT COUNT(*) FROM {quote_sqlite(table)}"
            ).fetchone()[0]
            postgres_count = pg_conn.execute(
                sql.SQL("SELECT COUNT(*) FROM public.{}").format(sql.Identifier(table))
            ).fetchone()[0]
            print(
                f"{table}: scanned={rows} sqlite={sqlite_count} "
                f"postgres={postgres_count} ok={postgres_count >= sqlite_count}",
                flush=True,
            )
    finally:
        pg_conn.close()
        sqlite_conn.close()


if __name__ == "__main__":
    main()
