import sqlite3
from datetime import datetime, timedelta, timezone

conn = sqlite3.connect(r'c:\Users\LEE\Documents\codex\ceo-briefing-platform\data\ceo_briefing.db')
cursor = conn.cursor()
new_time = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
cursor.execute("UPDATE app_settings SET value = ? WHERE key LIKE 'rss_last_checked_%'", (new_time,))
conn.commit()
print(f"Updated {cursor.rowcount} settings.")
conn.close()
