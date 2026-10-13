"""Runner for the close-verify schema migration — dry-run by default, one transaction per direction.

Contract enforced here (mirrors AGI-01 §11 without the ledger table):

  * **dry-run default**: `dry_run=True` unless the CLI was called with `--apply` *and* an approval
    reference, so a scheduled or one-shot run cannot apply it by accident.
  * **one transaction per direction**: a failure mid-way rolls back — no partial application.
  * **idempotent**: the applied state is detected from the CATALOG (does the column exist? does its
    COMMENT carry our checksum?), not from a ledger row, so a re-run is `already_applied` with zero
    statements executed.
  * **drift refusal**: column present with a different checksum, or present with no marker at all →
    `refused_drift` / `refused_unmarked` and nothing is executed. Silently adopting an unknown column
    is how two schemas fork.

`conn` is injected (the tests run the real DDL inside a throwaway PostgreSQL schema) and the
connection helper is the one production uses — `db_compat.connect_primary_db`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .close_verify_0001_reference_kind import COMMENT_MARKER

DEFAULT_SCHEMA = "public"


@dataclass
class MigrationResult:
    direction: str
    version: str
    schema: str
    status: str
    statements: tuple = ()
    checksum: str | None = None
    reason: str | None = None
    catalog: dict | None = None
    applied_by: str | None = None
    notes: list = field(default_factory=list)

    @property
    def executed(self) -> int:
        """How many statements this call actually ran (0 for dry-run / skipped / refused)."""
        return len(self.statements) if self.status == "applied" else 0

    def as_dict(self) -> dict:
        return {
            "direction": self.direction,
            "version": self.version,
            "schema": self.schema,
            "status": self.status,
            "statement_count": len(self.statements) if self.status == "applied" else 0,
            "statements": [s.render() for s in self.statements],
            "checksum": self.checksum,
            "reason": self.reason,
            "catalog": self.catalog,
            "applied_by": self.applied_by,
            "notes": list(self.notes),
        }


def catalog_state(conn, migration, schema: str = DEFAULT_SCHEMA) -> dict:
    """READ-ONLY: does the table/column exist, and what marker does the column carry?"""
    table_exists = conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
        (schema, migration.table)).fetchone() is not None
    column_exists = conn.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_schema = %s AND table_name = %s "
        "AND column_name = %s", (schema, migration.table, migration.column)).fetchone() is not None
    comment = None
    if column_exists:
        row = conn.execute(
            "SELECT col_description(c.oid, a.attnum) AS comment FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = %s "
            "WHERE n.nspname = %s AND c.relname = %s",
            (migration.column, schema, migration.table)).fetchone()
        if row is not None:
            comment = row["comment"] if hasattr(row, "keys") else row[0]
    recorded = None
    if comment and COMMENT_MARKER in comment:
        recorded = comment.split(COMMENT_MARKER, 1)[1].split()[0]
    return {
        "table_exists": table_exists,
        "column_exists": column_exists,
        "column_comment": comment,
        "recorded_checksum": recorded,
    }


def run(conn, migration, *, direction: str = "up", dry_run: bool = True,
        schema: str = DEFAULT_SCHEMA, applied_by: str | None = None) -> MigrationResult:
    if direction not in ("up", "down"):
        raise ValueError(f"direction must be 'up' or 'down', got {direction!r}")

    statements = migration.up(schema) if direction == "up" else migration.down(schema)
    cat = catalog_state(conn, migration, schema)
    result = MigrationResult(direction=direction, version=migration.version, schema=schema,
                             status="dry_run", statements=statements, checksum=migration.checksum,
                             catalog=cat)

    if direction == "up":
        if cat["column_exists"]:
            recorded = cat["recorded_checksum"]
            if recorded is None:
                result.status = "refused_unmarked"
                result.reason = (f"{schema}.{migration.table}.{migration.column} exists but carries no "
                                 f"'{COMMENT_MARKER}' marker — this migration cannot prove it created it; "
                                 f"a human must decide (nothing was executed)")
                result.statements = ()
                return result
            if recorded != migration.checksum:
                result.status = "refused_drift"
                result.reason = (f"recorded checksum {recorded} != current {migration.checksum} — the DDL "
                                 f"changed after it was applied; refusing to re-apply (nothing executed)")
                result.statements = ()
                return result
            result.status = "already_applied"
            result.reason = "column present with this migration's marker — nothing to do"
            result.statements = ()
            return result
    elif not cat["column_exists"]:
        result.status = "skipped"
        result.reason = "column absent — nothing to revert (the table itself is never dropped)"
        result.statements = ()
        return result

    if dry_run:
        result.reason = result.reason or (f"dry-run: {len(statements)} statement(s) would run; nothing "
                                          f"was executed")
        return result

    try:
        for stmt in statements:
            conn.execute(stmt.sql, stmt.params)
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - report, never leave a partial application
        conn.rollback()
        result.status = "failed"
        result.reason = f"{exc.__class__.__name__}: {exc}"
        result.statements = ()
        return result

    result.status = "applied"
    result.applied_by = applied_by
    result.reason = None
    result.catalog = catalog_state(conn, migration, schema)
    return result
