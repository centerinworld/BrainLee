#!/usr/bin/env python3
"""Merge PostgreSQL-primary changes from public into a staged cutover schema."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from config import DATABASE_URL  # noqa: E402

POSTGRES_URL = DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1)


def columns(conn: psycopg.Connection, schema: str, table: str) -> list[str]:
    return [row[0] for row in conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
        (schema, table),
    )]


def primary_key(conn: psycopg.Connection, schema: str, table: str) -> list[str]:
    return [row[0] for row in conn.execute(
        """
        SELECT a.attname
        FROM pg_index i
        JOIN pg_class c ON c.oid=i.indrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN unnest(i.indkey) WITH ORDINALITY AS k(attnum,ord) ON TRUE
        JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.attnum
        WHERE n.nspname=%s AND c.relname=%s AND i.indisprimary
        ORDER BY k.ord
        """,
        (schema, table),
    )]


def conflict_key(conn: psycopg.Connection, schema: str, table: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT i.indisprimary, array_agg(a.attname ORDER BY k.ord) AS columns
        FROM pg_index i
        JOIN pg_class c ON c.oid=i.indrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN unnest(i.indkey) WITH ORDINALITY AS k(attnum,ord) ON TRUE
        JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.attnum
        WHERE n.nspname=%s AND c.relname=%s AND i.indisunique AND i.indisvalid
        GROUP BY i.indexrelid,i.indisprimary
        ORDER BY i.indisprimary ASC, cardinality(array_agg(a.attname)) ASC
        """,
        (schema, table),
    ).fetchall()
    return list(rows[0][1]) if rows else []


def merge_table(conn: psycopg.Connection, stage: str, table: str) -> dict:
    public_columns = columns(conn, "public", table)
    stage_columns = columns(conn, stage, table)
    shared = [name for name in public_columns if name in set(stage_columns)]
    if not shared:
        return {"table": table, "action": "skipped_no_shared_columns"}
    public_count = conn.execute(
        sql.SQL("SELECT COUNT(*) FROM public.{}").format(sql.Identifier(table))
    ).fetchone()[0]
    stage_count = conn.execute(
        sql.SQL("SELECT COUNT(*) FROM {}.{}").format(sql.Identifier(stage), sql.Identifier(table))
    ).fetchone()[0]
    keys = conflict_key(conn, stage, table)
    if keys and all(key in shared for key in keys):
        protected = set(keys) | set(primary_key(conn, stage, table))
        updates = [name for name in shared if name not in protected]
        statement = sql.SQL("INSERT INTO {}.{} ({}) SELECT {} FROM public.{} ON CONFLICT ({}) ").format(
            sql.Identifier(stage), sql.Identifier(table),
            sql.SQL(", ").join(sql.Identifier(name) for name in shared),
            sql.SQL(", ").join(sql.Identifier(name) for name in shared),
            sql.Identifier(table),
            sql.SQL(", ").join(sql.Identifier(name) for name in keys),
        )
        if updates:
            statement += sql.SQL("DO UPDATE SET {} ").format(sql.SQL(", ").join(
                sql.SQL("{}=EXCLUDED.{}").format(sql.Identifier(name), sql.Identifier(name))
                for name in updates
            ))
        else:
            statement += sql.SQL("DO NOTHING ")
        conn.execute(statement)
        action = "upserted_by_primary_key"
    elif public_count > stage_count:
        # No stable conflict key exists. Public is newer, so preserve the
        # PostgreSQL-primary version as a whole instead of guessing row identity.
        conn.execute(sql.SQL("TRUNCATE {}.{}").format(sql.Identifier(stage), sql.Identifier(table)))
        conn.execute(sql.SQL("INSERT INTO {}.{} ({}) SELECT {} FROM public.{}").format(
            sql.Identifier(stage), sql.Identifier(table),
            sql.SQL(", ").join(sql.Identifier(name) for name in shared),
            sql.SQL(", ").join(sql.Identifier(name) for name in shared),
            sql.Identifier(table),
        ))
        action = "replaced_from_newer_public"
    else:
        action = "kept_staged_source"
    final_count = conn.execute(
        sql.SQL("SELECT COUNT(*) FROM {}.{}").format(sql.Identifier(stage), sql.Identifier(table))
    ).fetchone()[0]
    return {
        "table": table, "action": action, "public_before": public_count,
        "staged_before": stage_count, "staged_after": final_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schema", default="cutover_20260810")
    parser.add_argument("--only", nargs="*", default=[])
    args = parser.parse_args()
    with psycopg.connect(POSTGRES_URL) as conn:
        tables = [row[0] for row in conn.execute(
            """SELECT p.table_name FROM information_schema.tables p
               JOIN information_schema.tables s ON s.table_name=p.table_name
               WHERE p.table_schema='public' AND s.table_schema=%s
               ORDER BY p.table_name""",
            (args.schema,),
        )]
        if args.only:
            wanted = set(args.only)
            tables = [table for table in tables if table in wanted]
        for table in tables:
            result = merge_table(conn, args.schema, table)
            conn.commit()
            if result.get("action") != "kept_staged_source":
                print(result, flush=True)


if __name__ == "__main__":
    main()
