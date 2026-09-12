import sqlite3
import datetime

conn = sqlite3.connect(r'c:\Users\LEE\Documents\codex\ceo-briefing-platform\data\ceo_briefing.db', timeout=10)
cursor = conn.cursor()
cursor.execute("INSERT OR IGNORE INTO rss_sources (feed_type, name, url) VALUES ('company', 'NAVER 검색 - 한화에어로스페이스', 'naver-news://한화에어로스페이스')")
cursor.execute("INSERT OR IGNORE INTO rss_sources (feed_type, name, url) VALUES ('company', 'NAVER 검색 - LIG넥스원', 'naver-news://LIG넥스원')")
five_days_ago = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=5)).isoformat()
cursor.execute("UPDATE app_settings SET value = ? WHERE key LIKE 'rss_last_checked_%'", (five_days_ago,))
conn.commit()
print("Done")
conn.close()
