import sqlite3
import urllib.request
import json
import time

def summarize(api_key, model, title, text, link):
    prompt = (
        "Summarize this RSS article for an executive dashboard in one short Korean sentence.\n"
        f"Title: {title}\n"
        f"Summary: {text[:2000]}\n"
        f"Link: {link}\n"
        "Return only the summary sentence."
    )
    payload = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
    req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=payload, headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = json.loads(response.read().decode("utf-8"))
        return raw["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print("Error:", e)
        return ""

conn = sqlite3.connect(r'c:\Users\LEE\Documents\codex\ceo-briefing-platform\data\ceo_briefing.db', timeout=10)
conn.row_factory = sqlite3.Row
api_key = conn.execute("SELECT value FROM app_settings WHERE key = 'openai_api_key'").fetchone()[0]
model = "gpt-4o-mini"
cursor = conn.cursor()
rows = cursor.execute("SELECT id, title, summary, link FROM feed_items WHERE article_category = 'government' ORDER BY id DESC LIMIT 20").fetchall()
updated = 0
for r in rows:
    if len(r['summary']) > 150 or r['summary'] == r['title']:
        s = summarize(api_key, model, r['title'], r['summary'], r['link'])
        if s:
            conn.execute("UPDATE feed_items SET summary = ? WHERE id = ?", (s, r['id']))
            updated += 1
            print("Summarized:", r['title'])
        time.sleep(1)
conn.commit()
print(f"Summarized {updated} articles.")
conn.close()
