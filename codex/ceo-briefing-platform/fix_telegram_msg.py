with open(r"c:\Users\LEE\Documents\codex\ceo-briefing-platform\backend\services\rss_ingest.py", "r", encoding="utf-8") as f:
    content = f.read()

import re

# Update telegram alert message
content = re.sub(
    r'"🚨 <b>\[긴급/단독\] Breaking News</b> 🚨\\n\\n"',
    r'"🚨 <b>Breaking News</b> 🚨\\n\\n"',
    content
)

with open(r"c:\Users\LEE\Documents\codex\ceo-briefing-platform\backend\services\rss_ingest.py", "w", encoding="utf-8") as f:
    f.write(content)
print("Updated message")
