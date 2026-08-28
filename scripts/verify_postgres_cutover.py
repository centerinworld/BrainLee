#!/usr/bin/env python3
"""Audit the operational PostgreSQL cutover against the legacy SQLite source."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import IS_POSTGRES  # noqa: E402
from scripts.migrate_operational_postgres import (  # noqa: E402
    POSTGRES_URL,
    SQLITE_DB,
    table_names,
)
from macro_data_quality import PLAUSIBLE_CLOSE_RANGES, RETIRED_MACRO_SYMBOLS  # noqa: E402

REPORT_PATH = ROOT / "research_outputs" / "postgres_cutover" / "verification_latest.json"
RECOVERY_MANIFEST_PATH = REPORT_PATH.parent / "disaster_recovery_latest.json"
RESTORE_TEST_PATH = REPORT_PATH.parent / "restore_test_latest.json"
BACKUP_PATH = Path(
    "/Volumes/Realtek_NVME/stock_dashboard/postgres_public_pre_cutover_20260810.dump"
)
CORE_TABLES = (
    "price_history",
    "financial_data",
    "cash_flow_data",
    "dart_disclosures",
    "dilution_events",
    "backtest_runs",
    "backtest_run_specs",
    "tenbagger_results",
    "quant_major_indicator_series",
)


def sqlite_count(conn: sqlite3.Connection, table: str) -> int:
    quoted = '"' + table.replace('"', '""') + '"'
    return int(conn.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0])


def main() -> None:
    failures: list[str] = []
    # 2026-08-25: 기본 timeout(5s)로 연결해 06:10 아침 배치 혼잡 시간대에 다른 잡의
    # writer lock과 부딪히면 "database is locked"로 스크립트 전체가 크래시(stdout이
    # 비어 scheduler.py 쪽엔 "report parse failed"로만 보임 — 실제 원인이 가려져
    # 있었음). 다른 잡들이 쓰는 표준 timeout=30으로 맞춤.
    sqlite_conn = sqlite3.connect(str(SQLITE_DB), timeout=30)
    pg_conn = psycopg.connect(POSTGRES_URL)
    try:
        active_tables = table_names(sqlite_conn)
        pg_tables = {
            row[0]
            for row in pg_conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE'"
            )
        }
        missing = sorted(set(active_tables) - pg_tables)
        parity = []
        for table in active_tables:
            source_count = sqlite_count(sqlite_conn, table)
            if table not in pg_tables:
                parity.append(
                    {"table": table, "sqlite": source_count, "postgres": None, "delta": None}
                )
                continue
            target_count = int(
                pg_conn.execute(
                    sql.SQL("SELECT COUNT(*) FROM public.{}").format(sql.Identifier(table))
                ).fetchone()[0]
            )
            parity.append(
                {
                    "table": table,
                    "sqlite": source_count,
                    "postgres": target_count,
                    "delta": target_count - source_count,
                }
            )
        behind = [item for item in parity if item["delta"] is not None and item["delta"] < 0]
        if missing:
            failures.append(f"missing PostgreSQL tables: {', '.join(missing)}")
        if behind:
            failures.append(
                "PostgreSQL behind SQLite: "
                + ", ".join(f"{item['table']}({item['delta']})" for item in behind)
            )

        schemas = [
            row[0]
            for row in pg_conn.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name NOT LIKE 'pg_%' AND schema_name <> 'information_schema' "
                "ORDER BY schema_name"
            )
        ]
        index_count = int(
            pg_conn.execute("SELECT COUNT(*) FROM pg_indexes WHERE schemaname='public'").fetchone()[0]
        )
        core_counts = {
            table: int(
                pg_conn.execute(
                    sql.SQL("SELECT COUNT(*) FROM public.{}").format(sql.Identifier(table))
                ).fetchone()[0]
            )
            for table in CORE_TABLES
        }
        price_probe = pg_conn.execute(
            "SELECT MIN(date), MAX(date), COUNT(DISTINCT stock_code) FROM price_history"
        ).fetchone()
        database_bytes = int(
            pg_conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]
        )
        autotrade_enabled = os.getenv("STOCKEASY_LIVE_AUTOTRADE", "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if autotrade_enabled:
            failures.append("STOCKEASY_LIVE_AUTOTRADE must remain disabled")

        macro_quality: dict[str, int] = {}
        for symbol in sorted(RETIRED_MACRO_SYMBOLS):
            count = int(
                pg_conn.execute(
                    "SELECT COUNT(*) FROM price_history WHERE stock_code=%s", (symbol,)
                ).fetchone()[0]
            )
            macro_quality[symbol] = count
        for symbol, (minimum, maximum) in sorted(PLAUSIBLE_CLOSE_RANGES.items()):
            count = int(
                pg_conn.execute(
                    "SELECT COUNT(*) FROM price_history "
                    "WHERE stock_code=%s AND (close IS NULL OR close<%s OR close>%s)",
                    (symbol, minimum, maximum),
                ).fetchone()[0]
            )
            macro_quality[symbol] = count
        contaminated_macro_rows = sum(macro_quality.values())
        if contaminated_macro_rows:
            failures.append(f"macro price contamination detected: {contaminated_macro_rows} rows")

        recovery_evidence = {"manifest": None, "restore_test": None}
        try:
            manifest = json.loads(RECOVERY_MANIFEST_PATH.read_text())
            restore_test = json.loads(RESTORE_TEST_PATH.read_text())
            backup_file = Path(manifest["backup_path"])
            recovery_evidence = {"manifest": manifest, "restore_test": restore_test}
            if not backup_file.is_file():
                failures.append("latest disaster-recovery backup file is missing")
            elif backup_file.stat().st_size != manifest.get("backup_bytes"):
                failures.append("latest disaster-recovery backup size mismatch")
            if not restore_test.get("ok"):
                failures.append("latest PostgreSQL restore test did not pass")
            if restore_test.get("backup_path") != manifest.get("backup_path"):
                failures.append("restore test does not reference the latest backup")
            if restore_test.get("public_table_count") != manifest.get("public_table_count"):
                failures.append("restore-test public table count differs from backup manifest")
            if restore_test.get("core_counts") != manifest.get("core_counts"):
                failures.append("restore-test core counts differ from backup manifest")
            backup_age_days = (
                datetime.now() - datetime.fromisoformat(manifest["created_at"])
            ).total_seconds() / 86400
            restore_age_days = (
                datetime.now() - datetime.fromisoformat(restore_test["tested_at"])
            ).total_seconds() / 86400
            recovery_evidence["backup_age_days"] = round(backup_age_days, 3)
            recovery_evidence["restore_test_age_days"] = round(restore_age_days, 3)
            if backup_age_days > 8:
                failures.append(f"latest full backup is stale: {backup_age_days:.2f} days")
            if restore_age_days > 35:
                failures.append(f"latest restore test is stale: {restore_age_days:.2f} days")
        except Exception as exc:
            failures.append(f"recovery evidence unavailable: {exc}")

        report = {
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "ok": IS_POSTGRES and not failures,
            "is_postgres_primary": IS_POSTGRES,
            "public_table_count": len(pg_tables),
            "sqlite_active_table_count": len(active_tables),
            "missing_tables": missing,
            "postgres_behind": behind,
            "postgres_ahead": [item for item in parity if (item["delta"] or 0) > 0],
            "core_counts": core_counts,
            "price_history": {
                "min_date": price_probe[0],
                "max_date": price_probe[1],
                "stock_codes": price_probe[2],
            },
            "public_index_count": index_count,
            "schemas": schemas,
            "database_bytes": database_bytes,
            "autotrade_enabled": autotrade_enabled,
            "macro_price_quality": {
                "ok": contaminated_macro_rows == 0,
                "contaminated_rows": contaminated_macro_rows,
                "by_symbol": macro_quality,
            },
            "external_backup": {
                "path": str(BACKUP_PATH),
                "exists": BACKUP_PATH.exists(),
                "bytes": BACKUP_PATH.stat().st_size if BACKUP_PATH.exists() else 0,
            },
            "disaster_recovery": recovery_evidence,
            "known_constraint_exceptions": {
                "dart_employee_count": "nullable legacy composite primary key",
                "consensus_targets": "legacy duplicate natural key; non-unique lookup index",
                "dart_insider_holdings": "legacy duplicate natural key; non-unique lookup index",
            },
            "failures": failures,
        }
        if not IS_POSTGRES:
            report["failures"].append("PostgreSQL is not configured as primary")
            report["ok"] = False
    finally:
        pg_conn.close()
        sqlite_conn.close()

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
