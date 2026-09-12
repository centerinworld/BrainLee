import sqlite3
conn = sqlite3.connect('backend/db/app.db')
print(conn.execute("SELECT sql FROM sqlite_master WHERE name='rss_sources'").fetchone()[0])
