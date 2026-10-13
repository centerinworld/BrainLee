"""0001 — `price_close_verify_log.reference_kind` (source-time provenance), price verification.

Why this migration exists
-------------------------
`reference_kind` records WHICH source judged a run: `official_krx` (the KRX Open API sibling — final
by construction) or `fallback_kis` (the KIS daily bar — provisional until close+12h, because that
endpoint revises its own bar; measured 2026-10-11: 9 of 10 sampled codes changed 17h later).

Until now the *verifier script* added the column itself as a side effect of a scheduled run. That is
what this migration removes: a scheduled job must not ALTER a live table, and in particular must not
touch rows written before the column existed. Those legacy rows keep `reference_kind IS NULL` and are
exposed as **`unknown`** provenance — their source is never inferred from their timestamp or status.

Design rules (all asserted by tests/test_close_verify_migration_20261011.py)
---------------------------------------------------------------------------
* **No backfill, ever.** No statement may INSERT/UPDATE/DELETE a row. Adding a source attribution to
  a past row is guesswork, and the whole point of the column is that provenance is recorded at write
  time or not at all. `assert_no_data_statements()` enforces this mechanically.
* **Up and down are submitted together**; down removes only what up added — the column. It never
  drops the table (the verification history is not this migration's to delete), so a down is
  lossless for rows and loses only `reference_kind` values written after the migration.
* **Idempotent**: every statement is `IF NOT EXISTS`/`IF EXISTS`, and the applied state is detected
  from the CATALOG (column exists + comment marker matches), so a re-run is a no-op reported as
  `already_applied` with zero statements executed.
* **Drift refusal**: the applied column carries a `COMMENT` marker with this migration's checksum. If
  the column exists with a *different* checksum, or with no marker at all, apply is REFUSED (we cannot
  prove this migration created it, and silently adopting an unknown column is how schemas fork).
* **No new ledger table**: a one-column migration's idempotency comes from the catalog probe + the
  comment marker. Creating a `*_schema_migrations` table in `public` would itself be a second schema
  change needing its own approval.
* **Privileges are unchanged**: the table stays owner-only (`relacl IS NULL`, no PUBLIC grant). The
  migration neither grants nor revokes anything; tests pin that it did not.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

VERSION = "0001_close_verify_reference_kind"
NAME = "price_close_verify_log: reference_kind source-time provenance column"
TABLE = "price_close_verify_log"
COLUMN = "reference_kind"
COLUMN_TYPE = "text"
DEFAULT_SCHEMA = "public"

# The marker written into COMMENT ON COLUMN ... IS '<prefix><marker><checksum>'. Read back by the
# runner to decide between `already_applied` and drift refusal.
COMMENT_MARKER = "close_verify_provenance="
COMMENT_PREFIX = ("source-time provenance of the comparison reference: official_krx | fallback_kis. "
                  "NULL = unknown (row written before the column existed); never inferred. ")

EXPECTED_TABLE_COLUMNS = (
    "id", "checked_at", "trade_date", "sampled", "compared", "close_mismatch", "volume_mismatch",
    "mismatch_pct", "fetch_failed", "status", "detail",
)

# Verbs that would touch ROWS (as opposed to schema). ALTER/CREATE/COMMENT are allowed; GRANT/REVOKE
# are not (privileges must stay exactly as they are — owner-only on the live table).
_FORBIDDEN = ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "GRANT", "REVOKE")


@dataclass(frozen=True)
class Stmt:
    sql: str
    params: tuple = ()

    def render(self) -> str:
        return " ".join(self.sql.split())


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    table: str
    column: str
    checksum: str
    comment_marker: str = COMMENT_MARKER

    def up(self, schema: str = DEFAULT_SCHEMA) -> tuple[Stmt, ...]:
        return up_statements(schema, checksum=self.checksum)

    def down(self, schema: str = DEFAULT_SCHEMA) -> tuple[Stmt, ...]:
        return down_statements(schema)

    @property
    def up_statements(self) -> tuple[Stmt, ...]:
        """Default-schema view (what the CLI prints for `public`)."""
        return self.up()

    @property
    def down_statements(self) -> tuple[Stmt, ...]:
        return self.down()


def _quote(schema: str) -> str:
    return '"' + str(schema).replace('"', '""') + '"'


def up_statements(schema: str = DEFAULT_SCHEMA, *, checksum: str | None = None) -> tuple[Stmt, ...]:
    """DDL for one direction. `CREATE ... IF NOT EXISTS` keeps a fresh DB and the live DB identical."""
    q = _quote(schema)
    t = f"{q}.{TABLE}"
    marker = COMMENT_MARKER + (checksum if checksum is not None else compute_checksum())
    return (
        # 1) the table itself — the migration's TERMINAL shape (a fresh DB and the live DB converge;
        #    on live this is a no-op because the table already exists, and the ALTER below is the
        #    no-op that adds the column to it)
        Stmt(f"""CREATE TABLE IF NOT EXISTS {t} (
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
  detail TEXT NOT NULL DEFAULT '',
  {COLUMN} {COLUMN_TYPE}
)"""),
        # 2) the new column — the only thing added to an existing table
        Stmt(f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS {COLUMN} {COLUMN_TYPE}"),
        # 3) the marker that makes `already_applied` vs drift provable without a ledger table.
        #    COMMENT is a utility statement: PostgreSQL rejects bind parameters in it ($1), so the
        #    comment text is inlined as a quoted literal.
        Stmt(f"COMMENT ON COLUMN {t}.{COLUMN} IS '{(COMMENT_PREFIX + marker).replace(chr(39), chr(39) * 2)}'"),
    )


def down_statements(schema: str = DEFAULT_SCHEMA) -> tuple[Stmt, ...]:
    """Reverse of `up_statements`, dropping ONLY the column (never the table, never any row)."""
    q = _quote(schema)
    return (Stmt(f"ALTER TABLE {q}.{TABLE} DROP COLUMN IF EXISTS {COLUMN}"),)


def compute_checksum(schema: str = DEFAULT_SCHEMA) -> str:
    """sha256 over the up DDL text (the marker payload). Any edit to the DDL changes it."""
    text = "\n".join(s.render() for s in up_statements(schema, checksum=""))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def assert_no_data_statements() -> None:
    """Fail loudly if the migration ever grows a statement that touches ROWS.

    `reference_kind` must be recorded when a run is written, never back-filled: attributing a source
    to a past row is an inference, and an inference here is indistinguishable from a fabrication.
    """
    for direction, stmts in (("up", up_statements()), ("down", down_statements())):
        for s in stmts:
            head = s.render().upper()
            for verb in _FORBIDDEN:
                if head.startswith(verb):
                    raise AssertionError(
                        f"{direction} statement performs {verb}; this migration may only DDL "
                        f"(adding a provenance value to an existing row is forbidden): {s.render()}")


MIGRATION = Migration(
    version=VERSION,
    name=NAME,
    table=TABLE,
    column=COLUMN,
    checksum=compute_checksum(),
)

assert_no_data_statements()
