import sqlite3
conn = sqlite3.connect(r'c:\Users\LEE\Documents\codex\ceo-briefing-platform\data\ceo_briefing.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()
for row in c.execute("SELECT name, feed_type FROM rss_sources WHERE name LIKE '정부_%'"):
    print(row['name'], row['feed_type'])
conn.close()
