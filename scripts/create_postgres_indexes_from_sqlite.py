#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
from pathlib import Path

import psycopg
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SQLITE_DB = (ROOT / "stock.db").resolve()
DEFAULT_POSTGRES_URL = os.getenv(
    "POSTGRES_DATABASE_URL",
    os.getenv(
        "DATABASE_URL",
        "postgresql://stock_dashboard:stock_dashboard_local@127.0.0.1:5432/stock_dashboard",
    ),
)
DEFAULT_POSTGRES_URL = DEFAULT_POSTGRES_URL.replace("postgresql+psycopg://", "postgresql://", 1)


def quote_sqlite_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def create_indexes(sqlite_db: Path, postgres_url: str, tables: list[str]) -> None:
    src = sqlite3.connect(str(sqlite_db))
    src.row_factory = sqlite3.Row
    created = skipped = 0
    try:
        with psycopg.connect(postgres_url) as pg:
            for table in tables:
                indexes = src.execute(f"PRAGMA index_list({quote_sqlite_ident(table)})").fetchall()
                for index in indexes:
                    index_name = index["name"]
                    if index["partial"]:
                        skipped += 1
                        continue
                    columns = [
                        row["name"]
                        for row in src.execute(
                            f"PRAGMA index_info({quote_sqlite_ident(index_name)})"
                        ).fetchall()
                        if row["name"]
                    ]
                    if not columns:
                        skipped += 1
                        continue
                    if index_name.startswith("sqlite_autoindex_"):
                        if index["origin"] == "pk":
                            skipped += 1
                            continue
                        digest = hashlib.sha1(f"{table}:{','.join(columns)}".encode()).hexdigest()[:8]
                        index_name = f"uq_{table}_{digest}"
                    statement = sql.SQL("CREATE {unique} INDEX IF NOT EXISTS {index} ON {table} ({columns})").format(
                        unique=sql.SQL("UNIQUE") if index["unique"] else sql.SQL(""),
                        index=sql.Identifier(index_name),
                        table=sql.Identifier(table),
                        columns=sql.SQL(", ").join(sql.Identifier(column) for column in columns),
                    )
                    try:
                        pg.execute(statement)
                        pg.commit()
                    except psycopg.errors.UniqueViolation:
                        pg.rollback()
                        skipped += 1
                        print(f"{table}: skipped non-unique source key {index_name}")
                        continue
                    created += 1
                    print(f"{table}: {index_name} ({', '.join(columns)})")
    finally:
        src.close()
    print({"created_or_existing": created, "skipped": skipped})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite-db", type=Path, default=DEFAULT_SQLITE_DB)
    parser.add_argument("--postgres-url", default=DEFAULT_POSTGRES_URL)
    parser.add_argument("--tables", nargs="+", required=True)
    args = parser.parse_args()
    create_indexes(args.sqlite_db.resolve(), args.postgres_url, args.tables)


if __name__ == "__main__":
    main()
