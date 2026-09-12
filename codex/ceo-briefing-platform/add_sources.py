import sqlite3
import datetime

conn = sqlite3.connect(r'c:\Users\LEE\Documents\codex\ceo-briefing-platform\data\ceo_briefing.db')
cursor = conn.cursor()

# 1. Add RSS sources for Hanwha and LIG D&A
cursor.execute("INSERT OR IGNORE INTO rss_sources (feed_type, name, url) VALUES (?, ?, ?)", 
               ("company", "NAVER 검색 - 한화에어로스페이스", "naver-news://한화에어로스페이스"))
cursor.execute("INSERT OR IGNORE INTO rss_sources (feed_type, name, url) VALUES (?, ?, ?)", 
               ("company", "NAVER 검색 - LIG넥스원", "naver-news://LIG넥스원"))

# 2. Reset time to 5 days ago to fetch 5 days of articles
five_days_ago = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=5)).isoformat()
cursor.execute("UPDATE app_settings SET value = ? WHERE key LIKE 'rss_last_checked_%'", (five_days_ago,))

conn.commit()
print("Added Hanwha and LIG sources. Reset sync time to 5 days ago.")
conn.close()
