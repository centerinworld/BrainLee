"""Route legacy stock.db callers to PostgreSQL in service subprocesses."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
root = str(PROJECT_ROOT)
if root not in sys.path:
    sys.path.insert(0, root)

from db_compat import install_sqlite_primary_router


install_sqlite_primary_router()
