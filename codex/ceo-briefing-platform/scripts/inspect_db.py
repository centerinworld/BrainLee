from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "ceo_briefing.db"


def print_table(conn: sqlite3.Connection, name: str) -> None:
    rows = conn.execute(f"SELECT * FROM {name} LIMIT 10").fetchall()
    print(f"\n[{name}] count={len(rows)}")
    for row in rows:
        print(row)


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        for table in ["pages", "page_permissions", "calendar_events", "update_requests", "rss_sources", "feed_items"]:
            print_table(conn, table)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
