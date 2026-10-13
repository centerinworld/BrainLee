"""Test support for the close-verify schema migration: throwaway PostgreSQL schema isolation.

Why a real temp schema
----------------------
The migration is refused on `public` until a Planner approves it, so the tests execute the real DDL
in a one-off schema (`close_verify_test_*`) and drop it afterwards. A SQLite stand-in or a mock could
not prove the things that matter here: `ALTER TABLE … ADD COLUMN IF NOT EXISTS` idempotency on a real
catalog, `COMMENT ON COLUMN` read back through `pg_attribute`/`col_description`, column ACLs
(`relacl IS NULL`) and what one transaction does on failure.

Connections use the production helper (`db_compat.connect_primary_db`) — a different connection
implementation would not catch the failures that matter. `public` is never written.
"""

from __future__ import annotations

import contextlib
import re
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from db_compat import connect_primary_db  # noqa: E402

from migrations import MIGRATION  # noqa: E402
from migrations.runner import run  # noqa: E402

TEMP_SCHEMA_PREFIX = "close_verify_test"

# The live table BEFORE this migration: 11 columns, no reference_kind (measured 2026-10-11 via
# information_schema on public.price_close_verify_log).
LEGACY_TABLE_DDL = """CREATE TABLE IF NOT EXISTS {table} (
  id BIGSERIAL PRIMARY KEY,
  checked_at TEXT NOT NULL DEFAULT to_char(now(),'YYYY-MM-DD HH24:MI:SS'),
  trade_date TEXT NOT NULL,
  sampled INTEGER NOT NULL,
  compared INTEGER NOT NULL,
  close_mismatch INTEGER NOT NULL,
  volume_mismatch INTEGER NOT NULL,
  mismatch_pct DOUBLE PRECISION NOT NULL,
  fetch_failed INTEGER NOT NULL,
  status TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT ''
)"""

# The 5 real rows in public.price_close_verify_log on 2026-10-11 (trade_date, checked_at, status,
# close_mismatch, compared) — used to prove legacy rows are exposed as `unknown` provenance.
LIVE_LEGACY_ROWS = (
    ("2026-10-08", "2026-10-08 19:30:07", "mismatch", 56, 80),
    ("2026-10-07", "2026-10-07 19:30:07", "mismatch", 59, 80),
    ("2026-10-06", "2026-10-06 19:30:06", "mismatch", 54, 80),
    ("2026-10-02", "2026-10-02 22:25:07", "ok", 0, 20),
    ("2026-09-23", "2026-09-25 16:04:50", "ok", 0, 80),
)

_WRITE_VERBS = ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "CREATE", "ALTER", "DROP", "GRANT", "COMMENT")


def new_schema_name() -> str:
    return f"{TEMP_SCHEMA_PREFIX}_{uuid.uuid4().hex[:10]}"


def schema_exists(conn, schema: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s", (schema,)
    ).fetchone() is not None


def columns_of(conn, schema: str, table: str) -> list[str]:
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s "
        "ORDER BY ordinal_position", (schema, table)).fetchall()
    return [r["column_name"] if hasattr(r, "keys") else r[0] for r in rows]


@contextlib.contextmanager
def isolated_schema(*, pre_create_legacy: bool = False, legacy_rows: tuple = ()):
    """Create a throwaway schema, optionally with the pre-migration table + rows, and always drop it."""
    schema = new_schema_name()
    conn = connect_primary_db(timeout=60)
    try:
        conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        conn.commit()
        if pre_create_legacy:
            conn.execute(LEGACY_TABLE_DDL.format(table=f'"{schema}".{MIGRATION.table}'))
            for trade_date, checked_at, status, cm, compared in legacy_rows or LIVE_LEGACY_ROWS:
                conn.execute(
                    f'INSERT INTO "{schema}".{MIGRATION.table} (checked_at, trade_date, sampled, compared, '
                    f"close_mismatch, volume_mismatch, mismatch_pct, fetch_failed, status, detail) "
                    f"VALUES (%s,%s,%s,%s,%s,0,0,0,%s,'legacy row')",
                    (checked_at, trade_date, compared, compared, cm, status))
            conn.commit()
        yield conn, schema
    finally:
        with contextlib.suppress(Exception):
            conn.rollback()
        with contextlib.suppress(Exception):
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            conn.commit()
        with contextlib.suppress(Exception):
            conn.close()


def apply_up(conn, schema: str, migration=MIGRATION, *, dry_run: bool = False):
    return run(conn, migration, direction="up", dry_run=dry_run, schema=schema, applied_by="pytest")


def apply_down(conn, schema: str, migration=MIGRATION, *, dry_run: bool = False):
    return run(conn, migration, direction="down", dry_run=dry_run, schema=schema, applied_by="pytest")


def public_fingerprint(conn) -> dict:
    """READ-ONLY snapshot of everything this slice must not touch."""
    tables = conn.execute(
        "SELECT count(*) AS n FROM information_schema.tables WHERE table_schema='public' "
        "AND table_type='BASE TABLE'").fetchone()
    log_cols = columns_of(conn, "public", MIGRATION.table)
    rows = conn.execute(f"SELECT count(*) AS n, COALESCE(max(id),0) AS m FROM public.{MIGRATION.table}").fetchone()
    return {
        "base_tables": tables["n"] if hasattr(tables, "keys") else tables[0],
        "log_columns": tuple(log_cols),
        "log_rows": (rows["n"], rows["m"]) if hasattr(rows, "keys") else (rows[0], rows[1]),
    }


class StatementRecorder:
    """Wraps a connection and records every statement — used to prove what was (not) executed."""

    def __init__(self, conn) -> None:
        self._conn = conn
        self.statements: list[str] = []

    def execute(self, sql, params=()):
        self.statements.append(" ".join(str(sql).split()))
        return self._conn.execute(sql, params)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def data_statements(self) -> list[str]:
        """Statements that would touch ROWS (the migration must never issue one)."""
        return [s for s in self.statements if s.upper().startswith(("INSERT", "UPDATE", "DELETE", "TRUNCATE"))]

    def write_statements(self) -> list[str]:
        return [s for s in self.statements
                if s.upper().startswith(("INSERT", "UPDATE", "DELETE", "TRUNCATE", "CREATE", "ALTER",
                                         "DROP", "GRANT", "COMMENT"))]

    def touching(self, table: str) -> list[str]:
        return [s for s in self.statements if re.search(rf"\b{re.escape(table)}\b", s, re.I)]
