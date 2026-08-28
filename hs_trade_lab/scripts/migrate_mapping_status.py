from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = ROOT_DIR / "data" / "hs_trade_lab.db"


def has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        if not has_column(conn, "hs_code_company_map", "mapping_status"):
            conn.execute(
                "ALTER TABLE hs_code_company_map ADD COLUMN mapping_status TEXT NOT NULL DEFAULT 'provisional'"
            )
        if not has_column(conn, "hs_sector_map", "mapping_status"):
            conn.execute(
                "ALTER TABLE hs_sector_map ADD COLUMN mapping_status TEXT NOT NULL DEFAULT 'provisional'"
            )

        conn.execute(
            "UPDATE hs_code_company_map SET mapping_status='exact' "
            "WHERE stock_code='140860' AND hs_code='9012101090'"
        )
        conn.execute(
            "UPDATE hs_code_company_map SET mapping_status='composite' "
            "WHERE stock_code='348210' AND hs_code IN ('9031419000','9031809070','9031809091')"
        )
        conn.execute(
            "UPDATE hs_sector_map SET mapping_status='exact' "
            "WHERE hs_code='9012101090'"
        )
        conn.execute(
            "UPDATE hs_sector_map SET mapping_status='composite' "
            "WHERE hs_code IN ('9031419000','9031809070','9031809091')"
        )
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
