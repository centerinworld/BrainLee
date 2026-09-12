import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
db_path = ROOT / "data" / "ceo_briefing.db"

def main():
    if not db_path.exists():
        print(f"DB not found at {db_path}!")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    print("--- Searching '조직' in all dates ---")
    rows_org = cursor.execute(
        """
        SELECT id, title, article_published_at, published_at, article_category, published
        FROM feed_items
        WHERE title LIKE '%조직%' OR title LIKE '%슬림%'
        ORDER BY COALESCE(article_published_at, published_at) DESC
        """
    ).fetchall()

    for idx, row in enumerate(rows_org, 1):
        pub_at = row['article_published_at'] or row['published_at']
        print(f"{idx}. [{row['article_category']}] (Pub: {pub_at}) {row['title']} (ID: {row['id']}, Published: {row['published']})")

    print("\n--- Searching '전략사령부' in all dates ---")
    rows_strat = cursor.execute(
        """
        SELECT id, title, article_published_at, published_at, article_category, published
        FROM feed_items
        WHERE title LIKE '%전략사령부%'
        ORDER BY COALESCE(article_published_at, published_at) DESC
        """
    ).fetchall()

    for idx, row in enumerate(rows_strat, 1):
        pub_at = row['article_published_at'] or row['published_at']
        print(f"{idx}. [{row['article_category']}] (Pub: {pub_at}) {row['title']} (ID: {row['id']}, Published: {row['published']})")

    conn.close()

if __name__ == "__main__":
    main()
