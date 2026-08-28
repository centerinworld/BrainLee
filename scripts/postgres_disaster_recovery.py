#!/usr/bin/env python3
"""Create, verify, and test-restore stock-dashboard PostgreSQL backups."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import DATABASE_URL, IS_POSTGRES  # noqa: E402
from macro_data_quality import PLAUSIBLE_CLOSE_RANGES, RETIRED_MACRO_SYMBOLS  # noqa: E402


DEFAULT_BACKUP_DIR = Path(
    "/Volumes/Realtek_NVME/stock_dashboard/postgresql16/backups"
)
POSTGRES_BIN = Path("/opt/homebrew/opt/postgresql@16/bin")
REPORT_DIR = ROOT / "research_outputs" / "postgres_cutover"
CORE_TABLES = (
    "stock_universe",
    "price_history",
    "financial_data",
    "cash_flow_data",
    "dart_disclosures",
    "dilution_events",
    "tenbagger_results",
    "backtest_runs",
    "quant_major_indicator_series",
)


def connection_info() -> tuple[str, str]:
    if not IS_POSTGRES:
        raise SystemExit("POSTGRES_DATABASE_URL is not active")
    parsed = urlparse(DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1))
    if not parsed.username or not parsed.path.strip("/"):
        raise SystemExit("PostgreSQL URL must contain user and database")
    return parsed.username, parsed.path.strip("/")


def postgres_command(
    tool: str,
    *args: str,
    database: str | None = None,
    stdin=None,
    stdout=None,
) -> subprocess.CompletedProcess:
    executable = POSTGRES_BIN / tool
    if not executable.is_file():
        raise SystemExit(f"native PostgreSQL tool is missing: {executable}")
    parsed = urlparse(DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1))
    command = [str(executable)]
    if database is not None:
        command.extend(
            [
                "-h",
                parsed.hostname or "127.0.0.1",
                "-p",
                str(parsed.port or 5432),
                "-U",
                unquote(parsed.username or ""),
                "-d",
                database,
            ]
        )
    command.extend(args)
    env = os.environ.copy()
    if parsed.password:
        env["PGPASSWORD"] = unquote(parsed.password)
    return subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        stdin=stdin,
        stdout=stdout,
        check=True,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_metadata(conn) -> dict:
    counts = {
        table: int(
            conn.execute(
                sql.SQL("SELECT COUNT(*) FROM public.{}").format(sql.Identifier(table))
            ).fetchone()[0]
        )
        for table in CORE_TABLES
    }
    table_count = int(
        conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='public' AND table_type='BASE TABLE'"
        ).fetchone()[0]
    )
    index_count = int(
        conn.execute("SELECT COUNT(*) FROM pg_indexes WHERE schemaname='public'").fetchone()[0]
    )
    version = str(conn.execute("SHOW server_version").fetchone()[0])
    contaminated_macro_rows = 0
    for symbol in RETIRED_MACRO_SYMBOLS:
        contaminated_macro_rows += int(
            conn.execute(
                "SELECT COUNT(*) FROM price_history WHERE stock_code=%s", (symbol,)
            ).fetchone()[0]
        )
    for symbol, (minimum, maximum) in PLAUSIBLE_CLOSE_RANGES.items():
        contaminated_macro_rows += int(
            conn.execute(
                "SELECT COUNT(*) FROM price_history "
                "WHERE stock_code=%s AND (close IS NULL OR close<%s OR close>%s)",
                (symbol, minimum, maximum),
            ).fetchone()[0]
        )
    return {
        "database": connection_info()[1],
        "server_version": version,
        "public_table_count": table_count,
        "public_index_count": index_count,
        "macro_contaminated_rows": contaminated_macro_rows,
        "core_counts": counts,
    }


def catalog_entry_count(path: Path) -> int:
    result = postgres_command("pg_restore", "-l", str(path), stdout=subprocess.PIPE)
    text = result.stdout.decode("utf-8", errors="replace")
    return sum(1 for line in text.splitlines() if line and not line.startswith(";"))


def backup(output: Path | None) -> dict:
    user, database = connection_info()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = output or DEFAULT_BACKUP_DIR / f"stock_dashboard_full_{timestamp}.dump"
    target = target.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".partial")
    if temporary.exists():
        temporary.unlink()
    url = DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(url) as snapshot_conn:
        snapshot_conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        snapshot_id = str(snapshot_conn.execute("SELECT pg_export_snapshot()").fetchone()[0])
        metadata = source_metadata(snapshot_conn)
        with temporary.open("wb") as sink:
            postgres_command(
                "pg_dump",
                "-Fc",
                "--no-owner",
                "--no-acl",
                f"--snapshot={snapshot_id}",
                database=database,
                stdout=sink,
            )
    temporary.replace(target)
    manifest = {
        "format_version": 1,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "backup_path": str(target),
        "backup_bytes": target.stat().st_size,
        "sha256": sha256_file(target),
        "catalog_entries": catalog_entry_count(target),
        **metadata,
    }
    manifest_path = target.with_suffix(target.suffix + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "disaster_recovery_latest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    return manifest


def load_manifest(path: Path) -> tuple[Path, dict]:
    backup_path = path.expanduser().resolve()
    manifest_path = backup_path.with_suffix(backup_path.suffix + ".manifest.json")
    if not backup_path.is_file() or not manifest_path.is_file():
        raise SystemExit("backup or adjacent manifest is missing")
    manifest = json.loads(manifest_path.read_text())
    actual_hash = sha256_file(backup_path)
    if actual_hash != manifest.get("sha256"):
        raise SystemExit("backup SHA-256 mismatch")
    if backup_path.stat().st_size != manifest.get("backup_bytes"):
        raise SystemExit("backup byte-size mismatch")
    return backup_path, manifest


def verify(path: Path) -> dict:
    backup_path, manifest = load_manifest(path)
    entries = catalog_entry_count(backup_path)
    if entries != manifest.get("catalog_entries"):
        raise SystemExit("backup catalog entry-count mismatch")
    return {
        "ok": True,
        "backup_path": str(backup_path),
        "sha256": manifest["sha256"],
        "backup_bytes": manifest["backup_bytes"],
        "catalog_entries": entries,
    }


def audit(max_backup_age_days: int, max_restore_age_days: int) -> dict:
    manifest = json.loads((REPORT_DIR / "disaster_recovery_latest.json").read_text())
    restore_test = json.loads((REPORT_DIR / "restore_test_latest.json").read_text())
    verified = verify(Path(manifest["backup_path"]))
    now = datetime.now()
    backup_age = (now - datetime.fromisoformat(manifest["created_at"])).total_seconds() / 86400
    restore_age = (now - datetime.fromisoformat(restore_test["tested_at"])).total_seconds() / 86400
    failures: list[str] = []
    if backup_age > max_backup_age_days:
        failures.append(f"backup is stale: {backup_age:.2f} days")
    if restore_age > max_restore_age_days:
        failures.append(f"restore test is stale: {restore_age:.2f} days")
    if not restore_test.get("ok"):
        failures.append("latest restore test failed")
    if restore_test.get("backup_path") != manifest.get("backup_path"):
        failures.append("latest restore test references a different backup")
    result = {
        "ok": not failures,
        "audited_at": now.isoformat(timespec="seconds"),
        "backup_age_days": round(backup_age, 3),
        "restore_test_age_days": round(restore_age, 3),
        "max_backup_age_days": max_backup_age_days,
        "max_restore_test_age_days": max_restore_age_days,
        "verification": verified,
        "failures": failures,
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "recovery_health_latest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    if failures:
        raise SystemExit(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def prune_backups(apply: bool) -> dict:
    dumps = sorted(
        DEFAULT_BACKUP_DIR.glob("stock_dashboard_full_*.dump"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    protected = set(dumps[:8])
    monthly_seen: set[str] = set()
    for path in dumps:
        try:
            stamp = path.stem.removeprefix("stock_dashboard_full_")[:6]
            if len(stamp) == 6 and stamp not in monthly_seen and len(monthly_seen) < 12:
                monthly_seen.add(stamp)
                protected.add(path)
        except Exception:
            protected.add(path)
    for report in (REPORT_DIR / "disaster_recovery_latest.json", REPORT_DIR / "restore_test_latest.json"):
        try:
            protected.add(Path(json.loads(report.read_text())["backup_path"]).resolve())
        except Exception:
            pass
    candidates = [path for path in dumps if path.resolve() not in {p.resolve() for p in protected}]
    deleted: list[str] = []
    if apply:
        for path in candidates:
            manifest = path.with_suffix(path.suffix + ".manifest.json")
            path.unlink()
            if manifest.exists():
                manifest.unlink()
            deleted.append(str(path))
    return {
        "ok": True,
        "apply": apply,
        "backup_count": len(dumps),
        "protected_count": len(protected),
        "candidates": [str(path) for path in candidates],
        "deleted": deleted,
    }


def admin_url(database: str) -> str:
    parsed = urlparse(DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1))
    return parsed._replace(path=f"/{database}").geturl()


def drop_database(admin_conn, target: str) -> None:
    admin_conn.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname=%s AND pid <> pg_backend_pid()",
        (target,),
    )
    admin_conn.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(target)))


def restore_database(path: Path, target: str, keep: bool, recovery: bool = False) -> dict:
    backup_path, manifest = load_manifest(path)
    user, source_database = connection_info()
    if target == source_database:
        raise SystemExit("refusing to restore over the operational database")
    required_prefix = "stock_dashboard_recovered_" if recovery else "stock_dashboard_restore_verify_"
    if not target.startswith(required_prefix):
        raise SystemExit(f"target database must start with {required_prefix}")

    with psycopg.connect(admin_url("postgres"), autocommit=True) as admin:
        exists = bool(
            admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", (target,)).fetchone()
        )
        if recovery and exists:
            raise SystemExit("refusing to replace an existing recovery database")
        if not recovery:
            drop_database(admin, target)
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target)))
    restored = False
    try:
        postgres_command(
            "pg_restore",
            "--no-owner",
            "--no-acl",
            "--exit-on-error",
            "--jobs=4",
            str(backup_path),
            database=target,
        )
        with psycopg.connect(admin_url(target)) as conn:
            counts = {
                table: int(
                    conn.execute(
                        sql.SQL("SELECT COUNT(*) FROM public.{}").format(sql.Identifier(table))
                    ).fetchone()[0]
                )
                for table in CORE_TABLES
            }
            table_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_type='BASE TABLE'"
                ).fetchone()[0]
            )
            macro_contaminated_rows = 0
            for symbol in RETIRED_MACRO_SYMBOLS:
                macro_contaminated_rows += int(
                    conn.execute(
                        "SELECT COUNT(*) FROM price_history WHERE stock_code=%s", (symbol,)
                    ).fetchone()[0]
                )
            for symbol, (minimum, maximum) in PLAUSIBLE_CLOSE_RANGES.items():
                macro_contaminated_rows += int(
                    conn.execute(
                        "SELECT COUNT(*) FROM price_history "
                        "WHERE stock_code=%s AND (close IS NULL OR close<%s OR close>%s)",
                        (symbol, minimum, maximum),
                    ).fetchone()[0]
                )
        expected = manifest["core_counts"]
        failures = {
            table: {"expected": expected[table], "restored": counts[table]}
            for table in CORE_TABLES
            if counts[table] != expected[table]
        }
        if table_count != manifest["public_table_count"]:
            failures["public_table_count"] = {
                "expected": manifest["public_table_count"], "restored": table_count
            }
        expected_macro = int(manifest.get("macro_contaminated_rows", 0))
        if macro_contaminated_rows != expected_macro or macro_contaminated_rows != 0:
            failures["macro_contaminated_rows"] = {
                "expected": expected_macro,
                "restored": macro_contaminated_rows,
            }
        restored = not failures
        result = {
            "ok": restored,
            "tested_at": datetime.now().isoformat(timespec="seconds"),
            "backup_path": str(backup_path),
            "target_database": target,
            "mode": "recovery" if recovery else "restore-test",
            "kept": keep or recovery,
            "core_counts": counts,
            "public_table_count": table_count,
            "macro_contaminated_rows": macro_contaminated_rows,
            "failures": failures,
        }
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        report_name = "recovery_latest.json" if recovery else "restore_test_latest.json"
        (REPORT_DIR / report_name).write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )
        if failures:
            raise SystemExit(json.dumps(result, ensure_ascii=False, indent=2))
        return result
    finally:
        if not restored:
            print(f"restore failed; database kept for inspection: {target}", file=sys.stderr)
        elif not keep and not recovery:
            with psycopg.connect(admin_url("postgres"), autocommit=True) as admin:
                drop_database(admin, target)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    backup_parser = sub.add_parser("backup")
    backup_parser.add_argument("--output", type=Path)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("backup", type=Path)
    restore_parser = sub.add_parser("restore-test")
    restore_parser.add_argument("backup", type=Path)
    restore_parser.add_argument(
        "--target",
        default=f"stock_dashboard_restore_verify_{datetime.now():%Y%m%d_%H%M%S}",
    )
    restore_parser.add_argument("--keep", action="store_true")
    recovery_parser = sub.add_parser("restore")
    recovery_parser.add_argument("backup", type=Path)
    recovery_parser.add_argument("--target", required=True)
    prune_parser = sub.add_parser("prune")
    prune_parser.add_argument("--apply", action="store_true")
    audit_parser = sub.add_parser("audit")
    audit_parser.add_argument("--max-backup-age-days", type=int, default=8)
    audit_parser.add_argument("--max-restore-age-days", type=int, default=35)
    args = parser.parse_args()

    if args.command == "backup":
        result = backup(args.output)
    elif args.command == "verify":
        result = verify(args.backup)
    elif args.command == "restore-test":
        result = restore_database(args.backup, args.target, args.keep)
    elif args.command == "restore":
        result = restore_database(args.backup, args.target, True, recovery=True)
    elif args.command == "prune":
        result = prune_backups(args.apply)
    else:
        result = audit(args.max_backup_age_days, args.max_restore_age_days)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
