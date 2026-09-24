#!/usr/bin/env python3
"""Reconcile the five explicitly audited SQLite-to-PostgreSQL cutover gaps."""
from __future__ import annotations

import argparse
import json
import sqlite3
from decimal import Decimal, InvalidOperation
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg import sql

from db_compat import _ORIGINAL_SQLITE_CONNECT
from db_utils import STOCK_DB_PATH
from config import DATABASE_URL
from scripts.migrate_operational_postgres import copy_table, create_indexes, convert
from scripts.sync_sqlite_bridge_delta import sync_table

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'research_outputs' / 'postgres_cutover' / 'reconciliation_latest.json'
MISSING = ('company_product_mix', 'gems_analyst_insights', 'gems_daily_learned_logs')
CAFE = 'cafe_stock_indicator_mappings'
INSIDER = 'dart_insider_holdings'
PSYCOPG_URL = DATABASE_URL.replace('postgresql+psycopg://', 'postgresql://', 1)


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def reconcile(apply: bool) -> dict:
    src = _ORIGINAL_SQLITE_CONNECT(f'file:{STOCK_DB_PATH}?mode=ro', uri=True, timeout=120)
    src.row_factory = sqlite3.Row
    report = {'generated_at': datetime.now().isoformat(timespec='seconds'), 'apply': apply, 'tables': {}}
    with psycopg.connect(PSYCOPG_URL) as pg:
        for table in MISSING:
            exists = pg.execute('SELECT to_regclass(%s)', (f'public.{table}',)).fetchone()[0]
            source_count = src.execute(f'SELECT COUNT(*) FROM {q(table)}').fetchone()[0]
            if apply and not exists:
                copied = copy_table(src, pg, 'public', table)
                create_indexes(src, pg, 'public', table)
                pg.commit()
            else:
                copied = 0
            report['tables'][table] = {'source': source_count, 'existed_before': bool(exists), 'copied': copied}

        cafe_before = pg.execute(f'SELECT COUNT(*) FROM {q(CAFE)}').fetchone()[0]
        cafe_source = src.execute(f'SELECT COUNT(*) FROM {q(CAFE)}').fetchone()[0]
        cafe_synced = sync_table(src, pg, CAFE, 500) if apply else 0
        if apply:
            pg.commit()
        report['tables'][CAFE] = {'source': cafe_source, 'before': cafe_before, 'synced': cafe_synced}

        key_columns = ('rcept_no', 'repror', 'sp_stock_lmp_cnt', 'sp_stock_lmp_irds_cnt')
        source_rows = src.execute(f'SELECT * FROM {q(INSIDER)}').fetchall()
        pg_rows = pg.execute(
            f'SELECT {", ".join(key_columns)} FROM {q(INSIDER)}'
        ).fetchall()
        def key(row):
            normalized = []
            for index, value in enumerate(row):
                if value is None:
                    normalized.append('∅')
                elif index in (2, 3):
                    try:
                        normalized.append(format(Decimal(str(value).replace(',', '')), 'f'))
                    except InvalidOperation:
                        normalized.append(str(value).replace(',', ''))
                else:
                    normalized.append(str(value))
            return tuple(normalized)
        pg_keys = {key(row) for row in pg_rows}
        missing_rows = [row for row in source_rows if key(tuple(row[name] for name in key_columns)) not in pg_keys]
        if apply and missing_rows:
            columns = [row[1] for row in src.execute(f'PRAGMA table_info({q(INSIDER)})') if row[1] != 'id']
            declared = {row[1]: row[2] for row in src.execute(f'PRAGMA table_info({q(INSIDER)})')}
            statement = sql.SQL('INSERT INTO public.{} ({}) VALUES ({}) ON CONFLICT DO NOTHING').format(
                sql.Identifier(INSIDER), sql.SQL(', ').join(map(sql.Identifier, columns)),
                sql.SQL(', ').join(sql.Placeholder() for _ in columns))
            with pg.cursor() as cursor:
                cursor.executemany(
                    statement,
                    [tuple(convert(row[name], declared[name]) for name in columns) for row in missing_rows],
                )
            pg.commit()
        report['tables'][INSIDER] = {'source': len(source_rows), 'before': len(pg_rows),
                                     'missing_natural_keys': len(missing_rows)}
    src.close()
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    print(json.dumps(reconcile(parser.parse_args().apply), ensure_ascii=False))
