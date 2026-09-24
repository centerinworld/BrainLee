#!/usr/bin/env python3
"""Restore the pre-repair security master backup created on 2026-09-08."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_utils import connect_stock_db


MASTER_BACKUP = "security_master_history_backup_codex_20260908"
SHARE_BACKUP = "security_share_history_backup_codex_20260908"


def restore(confirm: bool = False) -> dict:
    if not confirm:
        raise RuntimeError("restore requires --confirm")
    conn = connect_stock_db()
    try:
        conn.execute("BEGIN")
        conn.execute("DELETE FROM security_share_history")
        conn.execute(f"INSERT INTO security_share_history SELECT * FROM {SHARE_BACKUP}")
        conn.execute("DELETE FROM security_master_history")
        conn.execute(f"INSERT INTO security_master_history SELECT * FROM {MASTER_BACKUP}")
        master_rows = int(conn.execute("SELECT COUNT(*) FROM security_master_history").fetchone()[0])
        share_rows = int(conn.execute("SELECT COUNT(*) FROM security_share_history").fetchone()[0])
        conn.commit()
        return {"restored": True, "master_rows": master_rows, "share_rows": share_rows}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    print(restore(confirm=args.confirm))
