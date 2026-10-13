"""Migration registry for the close-verify log table (price verification).

Currently one migration. Kept separate from `agi/migrations` on purpose: AGI-01 §11 governs the AGI
schema and is still under contract review, and the price-verification path must not depend on a
package whose API may change. The *principles* are copied deliberately (up + down together, dry-run
default, one transaction per direction, idempotent re-run, drift refusal).

Import style: `scripts/migrations/` is importable both as a package (`from migrations import ...`
after adding `scripts/` to `sys.path`, which is what this repo's scripts/tests already do) and as a
namespace package via `scripts.migrations` — hence the relative import below.
"""

from __future__ import annotations

from .close_verify_0001_reference_kind import MIGRATION

MIGRATIONS = (MIGRATION,)
LATEST = MIGRATION

__all__ = ["MIGRATIONS", "MIGRATION", "LATEST"]
