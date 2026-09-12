from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "ceo_briefing.db"
sys.path.append(str(ROOT / "backend"))

from services.rss_ingest import import_sources  # noqa: E402


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        result = import_sources(conn)
        for entry in result["results"]:
            print(f"{entry['feed_type']}: {entry['source_name']} -> {entry['item_count']} items")
        print(f"Done. Total imported: {result['total_imported']}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
