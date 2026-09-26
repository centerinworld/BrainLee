"""Source-aware, multi-dimensional stock taxonomy storage.

The legacy ``stock_sector_tags`` table flattens unrelated concepts into one
``sector`` column.  These helpers keep the source vocabulary, classification
dimension, evidence and snapshot date separate.  Historical snapshots are
retained; readers select the latest successful snapshot per source.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable

from config import IS_POSTGRES


def make_node_key(taxonomy: str, dimension: str, value: str) -> str:
    raw = str(value or "").strip()
    slug = re.sub(r"[^0-9A-Za-z가-힣]+", "-", raw).strip("-").lower()[:48]
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    return f"{taxonomy}:{dimension}:{slug or 'node'}:{digest}"


def ensure_sector_taxonomy_tables(conn) -> None:
    ddl = [
        """
        CREATE TABLE IF NOT EXISTS sector_taxonomy_nodes (
            node_key TEXT PRIMARY KEY,
            taxonomy TEXT NOT NULL,
            dimension TEXT NOT NULL,
            external_code TEXT,
            name TEXT NOT NULL,
            parent_key TEXT,
            description TEXT,
            source_url TEXT,
            taxonomy_version TEXT NOT NULL DEFAULT '1',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_sector_taxonomy_nodes_scope
            ON sector_taxonomy_nodes(taxonomy, dimension, name)
        """,
        """
        CREATE TABLE IF NOT EXISTS stock_sector_membership_v2 (
            stock_code TEXT NOT NULL,
            node_key TEXT NOT NULL,
            source_system TEXT NOT NULL,
            source_snapshot_date TEXT NOT NULL,
            confidence INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'observed',
            is_primary INTEGER NOT NULL DEFAULT 0,
            evidence_json TEXT,
            observed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (stock_code, node_key, source_system, source_snapshot_date)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_sector_membership_v2_stock
            ON stock_sector_membership_v2(stock_code, source_snapshot_date)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_sector_membership_v2_node
            ON stock_sector_membership_v2(node_key, source_snapshot_date)
        """,
        """
        CREATE TABLE IF NOT EXISTS sector_taxonomy_source_runs (
            run_key TEXT PRIMARY KEY,
            source_system TEXT NOT NULL,
            snapshot_date TEXT NOT NULL,
            status TEXT NOT NULL,
            node_count INTEGER NOT NULL DEFAULT 0,
            membership_count INTEGER NOT NULL DEFAULT 0,
            source_url TEXT,
            payload_hash TEXT,
            error_message TEXT,
            started_at TEXT,
            completed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            metadata_json TEXT
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_sector_taxonomy_runs_source
            ON sector_taxonomy_source_runs(source_system, snapshot_date)
        """,
    ]
    if IS_POSTGRES:
        # PostgresCompatConnection.executescript intentionally skips DDL because
        # legacy startup scripts must not mutate the cutover schema.  This is an
        # explicit migration helper, so issue the reviewed DDL statements one by
        # one instead.
        for statement in ddl:
            conn.execute(statement)
    else:
        conn.executescript(";\n".join(ddl) + ";")


def upsert_node(
    conn,
    *,
    node_key: str,
    taxonomy: str,
    dimension: str,
    name: str,
    external_code: str | None = None,
    parent_key: str | None = None,
    description: str | None = None,
    source_url: str | None = None,
    taxonomy_version: str = "1",
) -> None:
    conn.execute(
        """
        INSERT INTO sector_taxonomy_nodes
          (node_key, taxonomy, dimension, external_code, name, parent_key,
           description, source_url, taxonomy_version, is_active, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
        ON CONFLICT(node_key) DO UPDATE SET
          taxonomy=excluded.taxonomy,
          dimension=excluded.dimension,
          external_code=excluded.external_code,
          name=excluded.name,
          parent_key=excluded.parent_key,
          description=excluded.description,
          source_url=excluded.source_url,
          taxonomy_version=excluded.taxonomy_version,
          is_active=1,
          updated_at=CURRENT_TIMESTAMP
        """,
        (
            node_key,
            taxonomy,
            dimension,
            external_code,
            name,
            parent_key,
            description,
            source_url,
            taxonomy_version,
        ),
    )


def replace_source_snapshot(
    conn,
    *,
    source_system: str,
    snapshot_date: str,
    memberships: Iterable[dict[str, Any]],
) -> int:
    """Idempotently replace one source snapshot while retaining older dates."""
    # Provider responses occasionally repeat the same member in one page.
    # Count and persist unique memberships so source-run metrics match storage.
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in memberships:
        deduped[(str(row["stock_code"]), str(row["node_key"]))] = row
    rows = list(deduped.values())
    conn.execute(
        "DELETE FROM stock_sector_membership_v2 WHERE source_system=? AND source_snapshot_date=?",
        (source_system, snapshot_date),
    )
    for row in rows:
        evidence = row.get("evidence_json")
        if not isinstance(evidence, str):
            evidence = json.dumps(evidence or {}, ensure_ascii=False, sort_keys=True)
        conn.execute(
            """
            INSERT INTO stock_sector_membership_v2
              (stock_code, node_key, source_system, source_snapshot_date,
               confidence, status, is_primary, evidence_json, observed_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(stock_code, node_key, source_system, source_snapshot_date)
            DO UPDATE SET
              confidence=excluded.confidence,
              status=excluded.status,
              is_primary=excluded.is_primary,
              evidence_json=excluded.evidence_json,
              updated_at=CURRENT_TIMESTAMP
            """,
            (
                row["stock_code"],
                row["node_key"],
                source_system,
                snapshot_date,
                int(row.get("confidence", 0)),
                row.get("status", "observed"),
                int(bool(row.get("is_primary", False))),
                evidence,
            ),
        )
    return len(rows)


def record_source_run(
    conn,
    *,
    source_system: str,
    snapshot_date: str,
    status: str,
    node_count: int = 0,
    membership_count: int = 0,
    source_url: str | None = None,
    payload_hash: str | None = None,
    error_message: str | None = None,
    metadata: dict[str, Any] | None = None,
    run_key: str | None = None,
) -> None:
    run_key = run_key or f"{source_system}:{snapshot_date}"
    conn.execute(
        """
        INSERT INTO sector_taxonomy_source_runs
          (run_key, source_system, snapshot_date, status, node_count,
           membership_count, source_url, payload_hash, error_message,
           started_at, completed_at, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)
        ON CONFLICT(run_key) DO UPDATE SET
          status=excluded.status,
          node_count=excluded.node_count,
          membership_count=excluded.membership_count,
          source_url=excluded.source_url,
          payload_hash=excluded.payload_hash,
          error_message=excluded.error_message,
          completed_at=CURRENT_TIMESTAMP,
          metadata_json=excluded.metadata_json
        """,
        (
            run_key,
            source_system,
            snapshot_date,
            status,
            node_count,
            membership_count,
            source_url,
            payload_hash,
            error_message,
            json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
        ),
    )


LATEST_MEMBERSHIPS_SQL = """
    SELECT m.*
    FROM stock_sector_membership_v2 m
    JOIN (
        SELECT source_system, MAX(snapshot_date) AS snapshot_date
        FROM sector_taxonomy_source_runs
        WHERE status='success'
        GROUP BY source_system
    ) latest
      ON latest.source_system=m.source_system
     AND latest.snapshot_date=m.source_snapshot_date
"""
