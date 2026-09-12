import sqlite3
import pprint
conn = sqlite3.connect(r'c:\Users\LEE\Documents\codex\ceo-briefing-platform\data\ceo_briefing.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

print("--- Categories ---")
for row in cursor.execute("SELECT article_category, count(*) as c FROM feed_items GROUP BY article_category"):
    print(f"{row['article_category']}: {row['c']}")

print("\n--- Feed Types ---")
for row in cursor.execute("SELECT feed_type, count(*) as c FROM feed_items GROUP BY feed_type"):
    print(f"{row['feed_type']}: {row['c']}")

print("\n--- Recent Items (Last 15) ---")
for row in cursor.execute("SELECT id, title, source, feed_type, article_category FROM feed_items ORDER BY id DESC LIMIT 15"):
    print(f"[{row['feed_type']}] [{row['article_category']}] {row['source']}: {row['title']}")

conn.close()
