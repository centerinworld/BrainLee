import sqlite3
conn = sqlite3.connect(r'c:\Users\LEE\Documents\codex\ceo-briefing-platform\data\ceo_briefing.db')
cursor = conn.cursor()
cursor.execute("UPDATE feed_items SET article_category = 'hanwha' WHERE article_category = 'competitor'")
conn.commit()
print(f"Updated {cursor.rowcount} articles from competitor to hanwha.")
conn.close()
