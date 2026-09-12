import re

with open(r"c:\Users\LEE\Documents\codex\ceo-briefing-platform\backend\services\rss_ingest.py", "r", encoding="utf-8") as f:
    content = f.read()

# Add space to categories
content = content.replace(
    '''CATEGORY_LIG = "lig"
CATEGORY_REFERENCE = "reference"
ALLOWED_CATEGORIES = {CATEGORY_KAI, CATEGORY_GOVERNMENT, CATEGORY_HANWHA, CATEGORY_LIG, CATEGORY_REFERENCE}''',
    '''CATEGORY_LIG = "lig"
CATEGORY_SPACE = "space"
CATEGORY_REFERENCE = "reference"
ALLOWED_CATEGORIES = {CATEGORY_KAI, CATEGORY_GOVERNMENT, CATEGORY_HANWHA, CATEGORY_LIG, CATEGORY_SPACE, CATEGORY_REFERENCE}'''
)

# Add SPACE_KEYWORDS
content = content.replace(
    '''LIG_KEYWORDS = [
    "lig넥스원",
    "lig",
]''',
    '''LIG_KEYWORDS = [
    "lig넥스원",
    "lig",
]

SPACE_KEYWORDS = [
    "우주항공청",
    "kasa",
    "우주발사체",
    "위성",
    "누리호",
    "스페이스x",
    "우주산업",
    "우주탐사",
]'''
)

# Add space rule classification
content = content.replace(
    '''    if category == CATEGORY_LIG:
        return has_any_keyword(title, LIG_KEYWORDS)''',
    '''    if category == CATEGORY_LIG:
        return has_any_keyword(title, LIG_KEYWORDS)

    if category == CATEGORY_SPACE:
        return has_any_keyword(title, SPACE_KEYWORDS)'''
)

content = content.replace(
    '''    lig_hit = has_any_keyword(text, LIG_KEYWORDS)
    government_hit = has_any_keyword(text, GOVERNMENT_KEYWORDS)''',
    '''    lig_hit = has_any_keyword(text, LIG_KEYWORDS)
    space_hit = has_any_keyword(text, SPACE_KEYWORDS)
    government_hit = has_any_keyword(text, GOVERNMENT_KEYWORDS)'''
)

content = content.replace(
    '''    # 3) 정부/정책/예산/무기체계 중심 기사
    if government_hit and kai_mentioned:
        return CATEGORY_GOVERNMENT''',
    '''    # 3) 우주항공 분야 기사
    if space_hit and not kai_focus_in_title:
        return CATEGORY_SPACE

    # 4) 정부/정책/예산/무기체계 중심 기사
    if government_hit and kai_mentioned:
        return CATEGORY_GOVERNMENT'''
)

# Update OpenAI prompt
content = content.replace(
    '''- hanwha: 한화에어로스페이스, 한화시스템 등 한화 그룹 방산 중심 기사.
        - lig: LIG넥스원 중심 기사.
        - reference: KAI 제품 또는 사업 내용이 핵심이 아닌 기사.''',
    '''- hanwha: 한화에어로스페이스, 한화시스템 등 한화 그룹 방산 중심 기사.
        - lig: LIG넥스원 중심 기사.
        - space: 우주항공청, 위성, 발사체, 우주산업 등 우주 분야 중심 기사.
        - reference: KAI 제품 또는 사업 내용이 핵심이 아닌 기사.'''
)

with open(r"c:\Users\LEE\Documents\codex\ceo-briefing-platform\backend\services\rss_ingest.py", "w", encoding="utf-8") as f:
    f.write(content)
print("rss_ingest space done")
