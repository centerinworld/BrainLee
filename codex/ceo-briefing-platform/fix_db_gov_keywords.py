import sqlite3
conn = sqlite3.connect(r'c:\Users\LEE\Documents\codex\ceo-briefing-platform\data\ceo_briefing.db')
cursor = conn.cursor()
keywords = "KAI,한국항공우주산업,방위사업청,국방부,항공우주청,청와대,대통령실,방위산업,국방산업,우주산업,국방정책,항공기,방산,무기체계"
cursor.execute("UPDATE rss_sources SET include_keywords = ? WHERE name LIKE '정부_%'", (keywords,))
conn.commit()
print(f"Updated {cursor.rowcount} gov feeds with keywords.")
conn.close()
