#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import psycopg
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
SQLITE_DB = (ROOT / "stock.db").resolve()
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))
from config import DATABASE_URL as POSTGRES_URL  # noqa: E402
POSTGRES_URL = POSTGRES_URL.replace("postgresql+psycopg://", "postgresql://", 1)


def _ymd(days: int) -> str:
    return (date.today() - timedelta(days=days)).strftime("%Y%m%d")


def _iso(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


def _year(years: int) -> int:
    return date.today().year - years


TABLE_FILTERS: dict[str, tuple[str, tuple[Any, ...]]] = {
    "stock_universe": ("", ()),
    "price_history": ("WHERE date >= ?", (_iso(45),)),
    "financial_data": ("WHERE year >= ?", (_year(3),)),
    "cash_flow_data": ("WHERE year >= ?", (_year(3),)),
    "dilution_events": ("WHERE disclosed_at >= ?", (_iso(730),)),
    "broker_program_stock_daily": ("WHERE dt >= ?", (_iso(45),)),
    "cash_conversion_signals": ("WHERE fiscal_year >= ?", (_year(3),)),
    "contract_advance_signals": ("WHERE fiscal_year >= ?", (_year(3),)),
    "cost_breakdown": ("WHERE year >= ?", (_year(3),)),
    "cost_structure": ("WHERE year >= ?", (_year(3),)),
    "dart_contracts": ("WHERE disclosed_at >= ?", (_iso(730),)),
    "dart_disclosures": (
        "WHERE (rcept_dt LIKE '%-%' AND rcept_dt >= ?) OR (rcept_dt NOT LIKE '%-%' AND rcept_dt >= ?)",
        (_iso(730), _ymd(730)),
    ),
    "dart_insider_holdings": (
        "WHERE (rcept_dt LIKE '%-%' AND rcept_dt >= ?) OR (rcept_dt NOT LIKE '%-%' AND rcept_dt >= ?)",
        (_iso(730), _ymd(730)),
    ),
    "dart_material_purchase": ("WHERE year >= ?", (_year(3),)),
    "dart_backlog_quarterly": ("WHERE fiscal_year >= ?", (_year(3),)),
    "dart_rd_patent_signals": (
        "WHERE (rcept_dt LIKE '%-%' AND rcept_dt >= ?) OR (rcept_dt NOT LIKE '%-%' AND rcept_dt >= ?)",
        (_iso(730), _ymd(730)),
    ),
    "inventory_sales_signals": ("WHERE fiscal_year >= ?", (_year(3),)),
    "investor_flow_quarterly": ("WHERE year >= ?", (_year(3),)),
    "kiwoom_credit_balance": ("WHERE dt >= ?", (_iso(45),)),
    "kiwoom_foreign_flow": ("WHERE dt >= ?", (_iso(45),)),
    "margin_balance_daily": ("WHERE dt >= ?", (_iso(45),)),
    "order_backlog": ("WHERE year >= ?", (_year(3),)),
    "order_contracts": (
        "WHERE (rcept_dt LIKE '%-%' AND rcept_dt >= ?) OR (rcept_dt NOT LIKE '%-%' AND rcept_dt >= ?)",
        (_iso(730), _ymd(730)),
    ),
    "quant_major_indicator_series": ("", ()),
    "segment_revenue": ("WHERE year >= ?", (_year(3),)),
    "short_sell_daily": ("WHERE bas_dt >= ?", (_ymd(45),)),
    "stock_collection_config": ("", ()),
    "strategy_feature_snapshot": ("", ()),
    "treasury_buyback": (
        "WHERE (rcept_dt LIKE '%-%' AND rcept_dt >= ?) OR (rcept_dt NOT LIKE '%-%' AND rcept_dt >= ?)",
        (_iso(730), _ymd(730)),
    ),
    "valuation_history": ("WHERE year >= ?", (_year(3),)),
}
FORCE_PRIMARY_KEYS = {"dart_insider_holdings"}


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _conflict_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """Pick the ON CONFLICT target: natural key first, surrogate id as fallback.

    Tables re-collected via ``INSERT OR REPLACE`` or DELETE+INSERT (financial_data,
    cash_flow_data, short_sell_daily, segment_revenue, dilution_events, dart_contracts,
    etc. all confirmed to do this) regenerate a new surrogate ``id`` for a row that
    keeps the same natural key. Upserting on ``id`` alone then collides with the
    table's separate natural-key UNIQUE constraint in PostgreSQL (confirmed present
    on all of these via migrate_operational_postgres), because ON CONFLICT only
    suppresses the specific constraint it names. Always prefer the natural key when
    one exists; FORCE_PRIMARY_KEYS is the documented escape hatch for tables whose
    "natural" key is not actually unique in the legacy source (e.g. dart_insider_holdings).
    """
    columns = conn.execute(f"PRAGMA table_info({_quoted(table)})").fetchall()
    primary = [row["name"] for row in sorted(columns, key=lambda row: row["pk"]) if row["pk"]]
    natural: list[list[str]] = []
    for index in conn.execute(f"PRAGMA index_list({_quoted(table)})"):
        if not index["unique"]:
            continue
        names = [
            row["name"]
            for row in conn.execute(f"PRAGMA index_info({_quoted(index['name'])})")
            if row["name"]
        ]
        if names and "id" not in names:
            natural.append(names)
    if natural and table not in FORCE_PRIMARY_KEYS:
        return min(natural, key=len)
    if primary:
        return primary
    raise RuntimeError(f"{table}: no primary or unique key")


def _postgres_types(pg: psycopg.Connection, table: str) -> dict[str, str]:
    rows = pg.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s",
        (table,),
    ).fetchall()
    return {name: data_type for name, data_type in rows}


def _convert(value: Any, pg_type: str) -> Any:
    if value is None:
        return None
    if pg_type == "boolean":
        return bool(value)
    if pg_type == "bigint" and isinstance(value, str):
        return int(float(value.replace(",", "").strip()))
    if pg_type == "numeric" and not isinstance(value, Decimal):
        return Decimal(str(value).replace(",", "").strip())
    if pg_type == "double precision" and isinstance(value, str):
        return float(value.replace(",", "").strip())
    return value


def _batches(rows: Iterable[sqlite3.Row], size: int) -> Iterable[list[sqlite3.Row]]:
    batch: list[sqlite3.Row] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def sync_table(
    src: sqlite3.Connection,
    pg: psycopg.Connection,
    table: str,
    *,
    batch_size: int,
) -> int:
    where, params = TABLE_FILTERS[table]
    columns = [row["name"] for row in src.execute(f"PRAGMA table_info({_quoted(table)})")]
    conflict = _conflict_columns(src, table)
    pg_types = _postgres_types(pg, table)
    if not columns or not pg_types:
        raise RuntimeError(f"{table}: schema missing")
    columns = [column for column in columns if column in pg_types]
    if conflict != ["id"] and "id" in columns:
        # SQLite's surrogate id has diverged from PostgreSQL's independent identity
        # sequence (each side autoincrements on its own). Copying the SQLite id
        # verbatim can collide with an unrelated existing PostgreSQL row that
        # already claimed that number. When upserting on a real natural key,
        # leave id out entirely so PostgreSQL's own IDENTITY sequence assigns it
        # for new rows, and never touch it on UPDATE.
        columns = [column for column in columns if column != "id"]
    update_columns = [column for column in columns if column not in conflict]
    statement = sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT ({}) DO UPDATE SET {}").format(
        sql.Identifier(table),
        sql.SQL(", ").join(sql.Identifier(column) for column in columns),
        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
        sql.SQL(", ").join(sql.Identifier(column) for column in conflict),
        sql.SQL(", ").join(
            sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(column), sql.Identifier(column))
            for column in update_columns
        ),
    )
    query = f"SELECT * FROM {_quoted(table)} {where}"
    total = 0
    with pg.cursor() as cursor:
        tracks_source_ids = conflict == ["id"]
        if tracks_source_ids:
            cursor.execute(
                "CREATE TEMP TABLE IF NOT EXISTS _tenbagger_sync_ids "
                "(id BIGINT PRIMARY KEY) ON COMMIT DELETE ROWS"
            )
            cursor.execute("TRUNCATE _tenbagger_sync_ids")
        for batch in _batches(src.execute(query, params), batch_size):
            payload = [
                tuple(_convert(row[column], pg_types[column]) for column in columns)
                for row in batch
            ]
            cursor.executemany(statement, payload)
            if tracks_source_ids:
                id_position = columns.index("id")
                cursor.executemany(
                    "INSERT INTO _tenbagger_sync_ids(id) VALUES (%s) ON CONFLICT DO NOTHING",
                    [(values[id_position],) for values in payload],
                )
            total += len(payload)
        if tracks_source_ids and total:
            pg_where = where.replace("%", "%%").replace("?", "%s")
            delete_prefix = f"WHERE ({pg_where[6:]}) AND" if pg_where else "WHERE"
            cursor.execute(
                sql.SQL("DELETE FROM {} AS target {} NOT EXISTS "
                        "(SELECT 1 FROM _tenbagger_sync_ids source WHERE source.id=target.id)").format(
                    sql.Identifier(table),
                    sql.SQL(delete_prefix),
                ),
                params,
            )
    pg.execute(sql.SQL("ANALYZE {}").format(sql.Identifier(table)))
    return total


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tables", nargs="*", choices=sorted(TABLE_FILTERS), default=[])
    parser.add_argument("--batch-size", type=int, default=5000)
    args = parser.parse_args()
    if not POSTGRES_URL.startswith(("postgresql://", "postgresql+psycopg://")):
        raise SystemExit("POSTGRES_DATABASE_URL is not configured")

    tables = args.tables or list(TABLE_FILTERS)
    src = sqlite3.connect(f"file:{SQLITE_DB}?mode=ro", uri=True, timeout=60)
    src.row_factory = sqlite3.Row
    try:
        with psycopg.connect(POSTGRES_URL) as pg:
            for table in tables:
                total = sync_table(src, pg, table, batch_size=args.batch_size)
                pg.commit()
                print(f"{table}: {total:,} rows upserted", flush=True)
    finally:
        src.close()


if __name__ == "__main__":
    main()
