import sqlite3
import datetime
import traceback
from pathlib import Path
from backend.services.rss_ingest import import_sources

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "ceo_briefing.db"

def run_sync():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.isolation_level = None
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        res = import_sources(conn)
        print("Sync complete:", res)
    except Exception as e:
        print("Error:", e)
        traceback.print_exc()
    conn.close()

if __name__ == '__main__':
    run_sync()
