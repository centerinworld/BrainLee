import sqlite3
from pathlib import Path
from backend.services.rss_ingest import import_sources, send_telegram_briefing

ROOT = Path(__file__).resolve().parent

def main():
    db_path = ROOT / "backend" / "data" / "ceo_briefing.db"
    if not db_path.exists():
        alternate_path = ROOT / "data" / "ceo_briefing.db"
        if alternate_path.exists():
            db_path = alternate_path
        else:
            print(f"Database not found! Tried {db_path} and {alternate_path}")
            return

    print(f"Connecting to database at {db_path}...")
    conn = sqlite3.connect(db_path, timeout=30)
    conn.isolation_level = None
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")

    try:
        print("1. Running RSS Sources Synchronization...")
        res = import_sources(conn)
        print("Synchronization completed successfully:", res)

        import sys
        # 회장님의 엄격한 지침에 따라, 수동 싱크 독립 실행 시에는 실수로라도 실시간 텔레그램 발송이 불가능하도록 원천 차단(dry_run=True 강제 고정)
        print("\n2. Running DRY RUN Telegram Briefing (to print on console)...")
        send_telegram_briefing(conn, force=True, dry_run=True)
        print("DRY RUN Telegram briefing triggered successfully!")

    except Exception as e:
        print("Error occurred during execution:", e)
        import traceback
        traceback.print_exc()
    finally:
        conn.close()

if __name__ == "__main__":
    main()
