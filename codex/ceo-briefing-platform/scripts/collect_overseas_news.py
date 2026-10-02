"""
collect_overseas_news.py — GitHub Actions용 해외 뉴스 RSS 수집 스크립트

실행:
  python3 scripts/collect_overseas_news.py

환경 변수:
  NEWSINFO_API_URL   - API 서버 주소 (기본: https://api.newsinfo.cloud)
  NEWSINFO_API_TOKEN - API 토큰 (GitHub Secret)
  FEED_TYPE          - 'company' / 'competitor' / 'both' (기본: both)
  MAX_ITEMS          - 소스당 최대 수집 건수 (기본: 20)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

try:
    import feedparser
except ImportError:
    print("[ERROR] feedparser 미설치. pip install feedparser")
    sys.exit(1)

ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = ROOT / "data" / "overseas-news-config.json"
OUTPUT_FILE = ROOT / "data" / "overseas-news-latest.json"

API_URL   = os.getenv("NEWSINFO_API_URL", "https://api.newsinfo.cloud")
API_TOKEN = os.getenv("NEWSINFO_API_TOKEN", "")
FEED_TYPE = os.getenv("FEED_TYPE", "both")
MAX_ITEMS = int(os.getenv("MAX_ITEMS", "20"))


def fetch_feed(source: dict) -> list[dict]:
    url = source["url"]
    name = source["name"]
    try:
        parsed = feedparser.parse(url)
        items = []
        for entry in parsed.entries[:MAX_ITEMS]:
            published = ""
            if hasattr(entry, "published"):
                published = entry.published
            elif hasattr(entry, "updated"):
                published = entry.updated

            summary = ""
            if hasattr(entry, "summary"):
                summary = entry.summary[:500] if entry.summary else ""
            elif hasattr(entry, "description"):
                summary = entry.description[:500] if entry.description else ""

            items.append({
                "id": getattr(entry, "id", entry.get("link", "")),
                "title": getattr(entry, "title", "제목 없음"),
                "link": getattr(entry, "link", ""),
                "summary": summary,
                "published": published,
                "source_name": name,
                "category": source.get("category", "해외"),
                "lang": source.get("lang", "en"),
            })
        print(f"  [OK] {name}: {len(items)}건 수집")
        return items
    except Exception as exc:
        print(f"  [FAIL] {name}: {exc}")
        return []


def push_to_api(items: list[dict]) -> bool:
    """수집한 뉴스를 newsinfo.cloud API로 전송"""
    if not API_TOKEN:
        print("[SKIP] NEWSINFO_API_TOKEN 없음 — API 전송 생략, JSON 파일만 저장")
        return False

    payload = json.dumps({"items": items, "source": "github-actions"}).encode()
    endpoint = f"{API_URL}/api/overseas-news/sync"
    req = urllib.request.Request(
        endpoint,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_TOKEN}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            print(f"[API] 전송 완료: {result}")
            return True
    except urllib.error.HTTPError as e:
        print(f"[API] 전송 실패 ({e.code}): {e.read().decode()[:200]}")
        return False
    except Exception as exc:
        print(f"[API] 전송 오류: {exc}")
        return False


def main() -> None:
    print(f"=== 해외 뉴스 수집 시작 ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}) ===")

    if not CONFIG_FILE.exists():
        print(f"[ERROR] 설정 파일 없음: {CONFIG_FILE}")
        sys.exit(1)

    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    sources = config.get("sources", [])
    print(f"소스 수: {len(sources)}개")

    all_items: list[dict] = []
    for source in sources:
        items = fetch_feed(source)
        all_items.extend(items)

    # 중복 제거 (link 기준)
    seen = set()
    unique = []
    for item in all_items:
        key = item.get("link") or item.get("id")
        if key and key not in seen:
            seen.add(key)
            unique.append(item)

    print(f"\n수집 완료: 총 {len(unique)}건 (중복 제거 후)")

    # JSON 저장
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(unique),
        "sources": len(sources),
        "items": unique,
    }
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장: {OUTPUT_FILE}")

    # API 전송 시도
    push_to_api(unique)

    print("=== 완료 ===")


if __name__ == "__main__":
    main()
