from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = ROOT_DIR / "data" / "hs_trade_lab.db"
ROOT_STOCK_DB = ROOT_DIR.parent / "stock.db"


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS hs_codes (
            hs_code TEXT PRIMARY KEY,
            description TEXT DEFAULT '',
            category_label TEXT DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS companies (
            company_code TEXT PRIMARY KEY,
            name TEXT DEFAULT '',
            market TEXT DEFAULT '',
            sector_name TEXT DEFAULT '',
            is_listed TEXT DEFAULT 'Y',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    root = sqlite3.connect(f"file:{ROOT_STOCK_DB}?mode=ro", uri=True)
    root.row_factory = sqlite3.Row
    ensure_tables(conn)

    hs_rows = conn.execute(
        """
        SELECT hs_code, MAX(hs_name) AS description, MAX(sector_name) AS category_label
        FROM hs_code_company_map
        GROUP BY hs_code
        """
    ).fetchall()
    for hs_code, description, category_label in hs_rows:
        conn.execute(
            """
            INSERT INTO hs_codes (hs_code, description, category_label)
            VALUES (?, ?, ?)
            ON CONFLICT(hs_code) DO UPDATE SET
                description=excluded.description,
                category_label=CASE WHEN excluded.category_label <> '' THEN excluded.category_label ELSE hs_codes.category_label END,
                updated_at=CURRENT_TIMESTAMP
            """,
            (hs_code, description or "", category_label or ""),
        )

    sector_rows = conn.execute("SELECT hs_code, MAX(display_name) AS display_name FROM hs_sector_map GROUP BY hs_code").fetchall()
    for hs_code, display_name in sector_rows:
        conn.execute(
            """
            INSERT INTO hs_codes (hs_code, description, category_label)
            VALUES (?, ?, '')
            ON CONFLICT(hs_code) DO UPDATE SET
                description=CASE WHEN excluded.description <> '' THEN excluded.description ELSE hs_codes.description END,
                updated_at=CURRENT_TIMESTAMP
            """,
            (hs_code, display_name or ""),
        )

    company_rows = conn.execute(
        """
        SELECT DISTINCT stock_code, stock_name, sector_name
        FROM hs_code_company_map
        WHERE stock_code IS NOT NULL AND TRIM(stock_code) <> ''
        """
    ).fetchall()
    for stock_code, stock_name, sector_name in company_rows:
        market = ""
        root_row = root.execute(
            """
            SELECT market, COALESCE(sector_mid, sector_large, '') AS sector_name
            FROM stock_universe
            WHERE stock_code = ?
            ORDER BY base_date DESC
            LIMIT 1
            """,
            (stock_code,),
        ).fetchone()
        if root_row:
            market = root_row["market"] or ""
            sector_name = sector_name or root_row["sector_name"] or ""
        conn.execute(
            """
            INSERT INTO companies (company_code, name, market, sector_name, is_listed)
            VALUES (?, ?, ?, ?, 'Y')
            ON CONFLICT(company_code) DO UPDATE SET
                name=excluded.name,
                market=CASE WHEN excluded.market <> '' THEN excluded.market ELSE companies.market END,
                sector_name=CASE WHEN excluded.sector_name <> '' THEN excluded.sector_name ELSE companies.sector_name END,
                updated_at=CURRENT_TIMESTAMP
            """,
            (stock_code, stock_name or "", market, sector_name or ""),
        )

    conn.commit()
    print(
        {
            "hs_codes": conn.execute("SELECT COUNT(*) FROM hs_codes").fetchone()[0],
            "companies": conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0],
        }
    )
    root.close()
    conn.close()


if __name__ == "__main__":
    main()
