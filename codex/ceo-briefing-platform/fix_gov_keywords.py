import sqlite3
conn = sqlite3.connect(r'c:\Users\LEE\Documents\codex\ceo-briefing-platform\data\ceo_briefing.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

keywords = "KAI,한국항공우주산업,항공,방산,방위산업,국방,우주,무기,전투기,헬기,위성,발사체,KF-21,FA-50,수리온,LAH,우주청,KADEX,ADEX,수출,M&A,매각"
cursor.execute("UPDATE rss_sources SET include_keywords = ? WHERE name LIKE '정부_%'", (keywords,))

rows = cursor.execute("SELECT id, title, summary, link FROM feed_items WHERE feed_type='company' AND article_category IN ('reference', 'government')").fetchall()
deleted = 0
for r in rows:
    haystack = f"{r['title']} {r['summary']} {r['link']}".lower()
    matched = any(k.lower() in haystack for k in keywords.split(","))
    if not matched:
        cursor.execute("DELETE FROM feed_items WHERE id = ?", (r['id'],))
        deleted += 1

conn.commit()
print(f"Updated keywords and deleted {deleted} irrelevant articles.")
conn.close()
