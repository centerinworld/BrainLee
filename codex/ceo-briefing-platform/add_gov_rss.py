import re
import sys
sys.path.append(r"c:\Users\LEE\Documents\codex\ceo-briefing-platform")

# 1. Update rss_ingest.py
with open(r"c:\Users\LEE\Documents\codex\ceo-briefing-platform\backend\services\rss_ingest.py", "r", encoding="utf-8") as f:
    content = f.read()

content = content.replace(
    '''KAI_PRODUCT_KEYWORDS = [
    "kf-21",
    "fa-50",
    "t-50",
    "수리온",
    "lah",
    "소해헬기",
    "고정익",
    "회전익",
]''',
    '''KAI_PRODUCT_KEYWORDS = [
    "kf-21",
    "fa-50",
    "t-50",
    "수리온",
    "lah",
    "소해헬기",
    "고정익",
    "회전익",
    "상륙공격헬기",
    "마린온",
]'''
)

content = content.replace(
    '''SPACE_KEYWORDS = [
    "우주항공청",
    "kasa",
    "우주발사체",
    "위성",
    "누리호",
    "스페이스x",
    "우주산업",
    "우주탐사",
]''',
    '''SPACE_KEYWORDS = [
    "우주항공청",
    "kasa",
    "우주발사체",
    "위성",
    "누리호",
    "스페이스x",
    "우주산업",
    "우주탐사",
    "하이브리드 엔진",
]'''
)

with open(r"c:\Users\LEE\Documents\codex\ceo-briefing-platform\backend\services\rss_ingest.py", "w", encoding="utf-8") as f:
    f.write(content)

# 2. Add RSS feeds to database
from backend.db_access import add_rss_source, list_rss_sources

existing = list_rss_sources()
existing_urls = {s["url"] for s in existing}

feeds = [
    ("보도자료", "https://www.korea.kr/rss/pressrelease.xml"),
    ("사실은 이렇습니다", "https://www.korea.kr/rss/fact.xml"),
    ("부처 브리핑", "https://www.korea.kr/rss/ebriefing.xml"),
    ("청와대 브리핑", "https://www.korea.kr/rss/president.xml"),
    ("국무회의 브리핑", "https://www.korea.kr/rss/cabinet.xml"),
    ("연설문", "https://www.korea.kr/rss/speech.xml"),
    ("전문자료", "https://www.korea.kr/rss/expdoc.xml"),
]

for name, url in feeds:
    if url not in existing_urls:
        add_rss_source("company", "정부_" + name, url)

print("done")
