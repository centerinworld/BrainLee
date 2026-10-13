#!/usr/bin/env python3
"""Close-verify schema migration CLI — `price_close_verify_log.reference_kind` (source-time provenance).

Dry-run by default. Applying is a separate, explicitly approved act:

    # 1) ALWAYS start here — prints the statements, the checksum and the current catalog state
    ./venv/bin/python scripts/migrate_close_verify_schema.py

    # 2) only after a Planner approval, with the approval reference recorded
    ./venv/bin/python scripts/migrate_close_verify_schema.py --apply \
        --applied-by "code-doer" --approval-ref "planner: 2026-10-11 <message ref>"

    # 3) the reverse (drops ONLY the column; never the table, never a row) — also dry-run first
    ./venv/bin/python scripts/migrate_close_verify_schema.py --down
    ./venv/bin/python scripts/migrate_close_verify_schema.py --down --apply --applied-by … --approval-ref …

Refuses `--apply` without BOTH `--applied-by` and `--approval-ref` (exit 2) so the migration cannot be
applied by a stray scheduled/one-shot invocation. It performs no data statement: see
`scripts/migrations/close_verify_0001_reference_kind.py` (`assert_no_data_statements`).

Exit codes: 0 applied/skipped/already-applied/dry-run · 2 refused or approval missing · 3 failed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from db_compat import connect_primary_db  # noqa: E402
from migrations import MIGRATION  # noqa: E402
from migrations.runner import catalog_state, run  # noqa: E402

OK_STATUSES = {"applied", "dry_run", "already_applied", "skipped"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="execute (default is dry-run)")
    ap.add_argument("--down", action="store_true", help="reverse direction (drops ONLY the column)")
    ap.add_argument("--schema", default="public", help="schema to operate on (tests use a throwaway one)")
    ap.add_argument("--applied-by", default=None)
    ap.add_argument("--approval-ref", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    direction = "down" if a.down else "up"
    dry_run = not a.apply

    if a.apply and not (a.applied_by and a.approval_ref):
        print(json.dumps({
            "status": "approval_required",
            "reason": "--apply requires BOTH --applied-by and --approval-ref (a Planner approval "
                      "reference). Nothing was executed.",
            "version": MIGRATION.version, "direction": direction, "schema": a.schema,
        }, ensure_ascii=False))
        return 2

    conn = connect_primary_db(timeout=60)
    try:
        before = catalog_state(conn, MIGRATION, a.schema)
        result = run(conn, MIGRATION, direction=direction, dry_run=dry_run, schema=a.schema,
                     applied_by=a.applied_by)
        after = catalog_state(conn, MIGRATION, a.schema)
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    out = {"version": MIGRATION.version, "name": MIGRATION.name, **result.as_dict(),
           "catalog_before": before, "catalog_after": after,
           "dry_run": dry_run, "approval_ref": a.approval_ref}
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"[{MIGRATION.version}] direction={direction} schema={a.schema} "
              f"{'DRY-RUN' if dry_run else 'APPLY'} -> {result.status}")
        print(f"  checksum: {MIGRATION.checksum}")
        print(f"  catalog before: column_exists={before['column_exists']} "
              f"recorded_checksum={before['recorded_checksum']}")
        for s in result.statements:
            print(f"  SQL: {s.render()}")
        if result.reason:
            print(f"  reason: {result.reason}")
        print(f"  catalog after:  column_exists={after['column_exists']} "
              f"recorded_checksum={after['recorded_checksum']}")
        if not dry_run:
            print("  NOTE: no row was inserted/updated/deleted — legacy rows keep reference_kind IS NULL "
                  "(provenance 'unknown'); no source is inferred for past rows.")

    if result.status == "failed":
        return 3
    return 0 if result.status in OK_STATUSES else 2


if __name__ == "__main__":
    sys.exit(main())
