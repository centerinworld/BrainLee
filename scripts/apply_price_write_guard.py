#!/usr/bin/env python3
"""Install only the idempotent PostgreSQL historical-price write guard."""
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from db_compat import connect_primary_db
from price_integrity import install_write_guard

conn=connect_primary_db(timeout=60)
try:
    install_write_guard(conn)
    conn.commit()
    print({'price_history_basis_write_guard':'installed'})
finally:
    conn.close()
