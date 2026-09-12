import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "backend"))
from services.rss_ingest import import_sources

def main():
    db_path = ROOT / "data" / "ceo_briefing.db"
    print(f"Connecting to DB: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Clear last checked
        print("Clearing 'rss_last_checked_%'...")
        conn.execute("DELETE FROM app_settings WHERE key LIKE 'rss_last_checked_%'")
        conn.commit()

        print("Importing sources (backfill 30 days)...")
        result = import_sources(conn)
        for entry in result["results"]:
            print(f"{entry['feed_type']}: {entry['source_name']} -> {entry['item_count']} items")
        print(f"Done. Total imported: {result['total_imported']}")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
