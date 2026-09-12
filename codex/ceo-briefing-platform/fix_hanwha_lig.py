with open(r"c:\Users\LEE\Documents\codex\ceo-briefing-platform\backend\services\rss_ingest.py", "r", encoding="utf-8") as f:
    content = f.read()

import re

# Update HANWHA_KEYWORDS
content = re.sub(
    r"HANWHA_KEYWORDS = \[[^\]]*\]",
    '''HANWHA_KEYWORDS = [
    "한화에어로스페이스",
    "한화에어로",
    "한화시스템",
    "한화오션",
    "한화방산",
    "한화",
]''',
    content
)

# Update LIG_KEYWORDS
content = re.sub(
    r"LIG_KEYWORDS = \[[^\]]*\]",
    '''LIG_KEYWORDS = [
    "lig넥스원",
    "lig d&a",
    "lig",
]''',
    content
)

with open(r"c:\Users\LEE\Documents\codex\ceo-briefing-platform\backend\services\rss_ingest.py", "w", encoding="utf-8") as f:
    f.write(content)
print("Updated keywords")
