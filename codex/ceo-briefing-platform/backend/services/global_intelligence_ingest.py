# -*- coding: utf-8 -*-
"""
전세계 경제·통화당국·AI 연구기관 실제 RSS 수집 (2026-09-14, 소유자 지적).

배경: ceo_notebooklm_service.py의 "세계 경제"/"AI 기술" 섹션이 사실은
services/rss_ingest.py의 KAI(한국항공우주) 전용 뉴스 더미를 키워드로 재분류한
것에 불과했다 - 실제로 global_authority_count=0이었다(IMF/World Bank/Fed/
OpenAI 등 단 한 건도 없음). 원인: rss_ingest.py의 filter_items_by_keywords()가
모든 기사에 has_hangul(한글 포함 여부) + CORE_FILTER_KEYWORDS(KAI/방산 키워드)
매칭을 강제하는 하드 게이트라서, 영어로 된 진짜 글로벌 뉴스는 애초에 전부
걸러졌다.

이 모듈은 그 KAI 전용 게이트를 건드리지 않고(오랫동안 튜닝된 파이프라인을
건드리는 위험을 피하기 위해) 완전히 별도의 경로로, 실제로 응답을 확인한 RSS
피드만 가져와 feed_type='global_intelligence'로 feed_items에 저장한다.
services/rss_ingest.py의 순수 파서 유틸(fetch_xml/parse_feed/publisher_from_link)만
재사용하고, KAI 특화 로직(is_kai_core_story, 이벤트 앵커 매칭, AI 기반 중복
판정 등)은 전혀 거치지 않는다.
"""

from __future__ import annotations

import sqlite3
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from services.rss_ingest import parse_feed, publisher_from_link

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "ceo_briefing.db"

# 2026-09-14 직접 curl로 200 응답 + 유효 XML을 확인한 소스만 등록한다(추측/미검증 URL 금지).
GLOBAL_FEEDS: List[Dict[str, str]] = [
    {"name": "BBC Business News", "url": "http://feeds.bbci.co.uk/news/business/rss.xml"},
    {"name": "European Central Bank - Press", "url": "https://www.ecb.europa.eu/rss/press.html"},
    {"name": "US Federal Reserve - Press Releases", "url": "https://www.federalreserve.gov/feeds/press_all.xml"},
    {"name": "WSJ Markets", "url": "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain"},
    {"name": "OpenAI News", "url": "https://openai.com/news/rss.xml"},
    {"name": "Google DeepMind Blog", "url": "https://deepmind.google/blog/rss.xml"},
    {"name": "TechCrunch AI", "url": "https://techcrunch.com/category/artificial-intelligence/feed/"},
]


def _fetch_xml(url: str) -> bytes:
    # rss_ingest.fetch_xml()은 기본 urllib User-Agent를 쓰는데, federalreserve.gov/openai.com
    # 등 일부 실제 사이트가 이를 403으로 차단하는 걸 직접 확인했다(curl은 통과). KAI 파이프라인
    # 공용 함수는 건드리지 않고 이 모듈에서만 일반 브라우저 유사 UA를 쓴다.
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; AI-System-Briefing/1.0)"})
    with urllib.request.urlopen(req, timeout=15) as response:
        return response.read()


def _normalize_id(link: str, fallback: str) -> str:
    safe = "".join(ch for ch in link if ch.isalnum())[-40:]
    return f"global-{safe or fallback}"


def ingest_global_intelligence(conn: sqlite3.Connection) -> Dict[str, Any]:
    """등록된 실제 글로벌 피드를 가져와 신규 항목만 feed_items에 저장한다."""
    existing_links = {
        row[0] for row in conn.execute("SELECT link FROM feed_items WHERE link IS NOT NULL").fetchall()
    }
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    inserted = 0
    per_source: Dict[str, Dict[str, Any]] = {}
    for feed in GLOBAL_FEEDS:
        name, url = feed["name"], feed["url"]
        try:
            xml_bytes = _fetch_xml(url)
            items = parse_feed(xml_bytes)
            per_source[name] = {"status": "OK", "fetched": len(items), "error": None}
        except Exception as exc:
            per_source[name] = {"status": "FETCH_FAILED", "fetched": 0, "error": str(exc)[:300]}
            continue

        for idx, item in enumerate(items):
            link = str(item.get("link", "")).strip()
            title = str(item.get("title", "")).strip()
            if not link or not title or link in existing_links:
                continue
            existing_links.add(link)
            publisher = item.get("publisher") or publisher_from_link(link) or name
            row_id = _normalize_id(link, f"{name}-{idx}")
            conn.execute(
                """
                INSERT OR IGNORE INTO feed_items
                (id, feed_type, title, summary, link, source, selected, published,
                 published_at, article_published_at, article_publisher, article_category, category_manual)
                VALUES (?, 'global_intelligence', ?, ?, ?, ?, 0, 0, ?, ?, ?, 'global_intelligence', 0)
                """,
                (
                    row_id, title, item.get("summary") or title, link, name,
                    item.get("published_at") or now_iso, item.get("published_at") or now_iso, publisher,
                ),
            )
            inserted += 1
    conn.commit()
    return {"inserted": inserted, "sources": per_source}


if __name__ == "__main__":
    _conn = sqlite3.connect(DB_PATH)
    _conn.row_factory = sqlite3.Row
    result = ingest_global_intelligence(_conn)
    _conn.close()
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))
