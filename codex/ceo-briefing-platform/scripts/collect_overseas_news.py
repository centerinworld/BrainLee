"""
collect_overseas_news.py — AI 인사이트 + 텔레그램 전송 포함 해외 뉴스 수집

환경 변수:
  GEMINI_API_KEY     - Google Gemini API 키 (GitHub Secret)
  TELEGRAM_BOT_TOKEN - 텔레그램 봇 토큰 (GitHub Secret)
  TELEGRAM_CHAT_ID   - 텔레그램 채팅 ID (GitHub Secret)
  NEWSINFO_API_URL   - API 서버 주소 (기본: https://api.newsinfo.cloud)
  NEWSINFO_API_TOKEN - API 토큰 (GitHub Secret)
  FEED_TYPE          - 'company' / 'competitor' / 'both' (기본: both)
  MAX_ITEMS          - 소스당 최대 수집 건수 (기본: 20)
  TOP_N              - AI 분석 대상 상위 뉴스 수 (기본: 12)
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

try:
    import feedparser
except ImportError:
    print("[ERROR] feedparser 미설치. pip install feedparser")
    sys.exit(1)

try:
    import requests as _req
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = ROOT / "data" / "overseas-news-config.json"
OUTPUT_FILE = ROOT / "data" / "overseas-news-latest.json"

API_URL           = os.getenv("NEWSINFO_API_URL", "https://api.newsinfo.cloud")
API_TOKEN         = os.getenv("NEWSINFO_API_TOKEN", "")
GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID", "")
FEED_TYPE         = os.getenv("FEED_TYPE", "both")
MAX_ITEMS         = int(os.getenv("MAX_ITEMS", "20"))
TOP_N             = int(os.getenv("TOP_N", "12"))


# ── 1. RSS 수집 ──────────────────────────────────────────────────────────────

def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def fetch_feed(source: dict, weight: float = 1.0, keywords: list[str] = None) -> list[dict]:
    urls = [source["url"]]
    if source.get("fallback_url"):
        urls.append(source["fallback_url"])

    for url in urls:
        try:
            parsed = feedparser.parse(url)
            if not parsed.entries:
                continue
            items = []
            for entry in parsed.entries[:MAX_ITEMS]:
                published = getattr(entry, "published", "") or getattr(entry, "updated", "")
                raw_summary = (
                    getattr(entry, "summary", "")
                    or getattr(entry, "description", "")
                    or ""
                )
                summary = _strip_html(raw_summary)[:600]
                title = _strip_html(getattr(entry, "title", "제목 없음"))
                link = getattr(entry, "link", "")

                score = _score(title, summary, weight, keywords or [])

                items.append({
                    "id": getattr(entry, "id", link),
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "published": published,
                    "source_name": source["name"],
                    "category": source.get("category", "해외"),
                    "lang": source.get("lang", "en"),
                    "score": round(score, 3),
                })
            print(f"  [OK] {source['name']}: {len(items)}건")
            return items
        except Exception as exc:
            print(f"  [WARN] {source['name']} ({url}): {exc}")

    print(f"  [FAIL] {source['name']}: 모든 URL 실패")
    return []


def _score(title: str, summary: str, weight: float, keywords: list[str]) -> float:
    text = (title + " " + summary).lower()
    kw_hits = sum(1 for kw in keywords if kw in text)
    kw_score = min(kw_hits * 0.15, 1.0)
    return weight + kw_score


# ── 2. AI 인사이트 생성 ──────────────────────────────────────────────────────

def _gemini_request(prompt: str) -> str:
    """Gemini Flash API 직접 호출 (requests 없어도 작동)"""
    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-1.5-flash:generateContent"
        f"?key={GEMINI_API_KEY}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1200},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result["candidates"][0]["content"]["parts"][0]["text"].strip()


def generate_insight(top_items: list[dict]) -> dict:
    if not GEMINI_API_KEY:
        print("[SKIP] GEMINI_API_KEY 없음 — AI 인사이트 생략")
        return {}

    news_list = "\n".join(
        f"{i+1}. [{item['source_name']}] {item['title']}\n   {item['summary'][:200]}"
        for i, item in enumerate(top_items[:TOP_N])
    )

    prompt = f"""다음은 오늘 수집된 주요 해외 뉴스 {len(top_items[:TOP_N])}건입니다.

{news_list}

다음 형식으로 한국어로 분석해 주세요:

## 오늘의 핵심 3가지
(각 1-2문장, 투자자·경영자가 반드시 알아야 할 사실)

## 시장 영향 분석
(한국 주식·방산·경제에 미치는 영향, 3-5문장)

## 주목 종목/섹터
(오늘 뉴스와 관련된 한국 주식 섹터나 종목 방향성, 2-3문장)

## 내일 체크포인트
(내일 확인해야 할 발표·이벤트·데이터, 2-3항목)"""

    try:
        raw = _gemini_request(prompt)
        print(f"[Gemini] AI 인사이트 생성 완료 ({len(raw)}자)")
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": "gemini-1.5-flash",
            "content": raw,
            "news_count": len(top_items[:TOP_N]),
        }
    except Exception as exc:
        print(f"[Gemini] 오류: {exc}")
        return {}


# ── 3. 텔레그램 전송 ─────────────────────────────────────────────────────────

def _tg_api(token: str, method: str, payload: dict) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"[TG] {method} 오류: {exc}")
        return {}


def send_telegram_briefing(top_items: list[dict], insight: dict) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[SKIP] 텔레그램 설정 없음 — 전송 생략")
        return

    now_kst = datetime.now(timezone.utc).strftime("%m/%d %H:%M UTC")

    # 메시지 1: AI 인사이트
    if insight and insight.get("content"):
        insight_msg = (
            f"🌐 <b>해외 뉴스 AI 브리핑</b>  {now_kst}\n\n"
            + insight["content"][:3800]
        )
        _tg_api(TELEGRAM_BOT_TOKEN, "sendMessage", {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": insight_msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        })
        print("[TG] AI 인사이트 전송 완료")
        time.sleep(1)

    # 메시지 2: 상위 5개 헤드라인
    lines = [f"📰 <b>주요 해외 뉴스 TOP 5</b>  {now_kst}\n"]
    for i, item in enumerate(top_items[:5], 1):
        cat = item.get("category", "")
        lines.append(
            f"{i}. <b>[{cat}]</b> {item['title']}\n"
            f"   <a href='{item['link']}'>{item['source_name']}</a>"
        )

    headlines_msg = "\n".join(lines)
    _tg_api(TELEGRAM_BOT_TOKEN, "sendMessage", {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": headlines_msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    })
    print("[TG] 헤드라인 전송 완료")


# ── 4. API 전송 ───────────────────────────────────────────────────────────────

def push_to_api(items: list[dict]) -> bool:
    if not API_TOKEN:
        return False
    payload = json.dumps({"items": items, "source": "github-actions"}).encode()
    endpoint = f"{API_URL}/api/overseas-news/sync"
    req = urllib.request.Request(
        endpoint, data=payload, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {API_TOKEN}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"[API] 전송 완료: {json.loads(resp.read())}")
            return True
    except Exception as exc:
        print(f"[API] 전송 오류: {exc}")
        return False


# ── 5. 메인 ───────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"=== 해외 뉴스 수집 시작 ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}) ===")

    if not CONFIG_FILE.exists():
        print(f"[ERROR] 설정 파일 없음: {CONFIG_FILE}")
        sys.exit(1)

    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    sources = config.get("sources", [])
    weights = config.get("source_weights", {})
    keywords = config.get("importance_keywords", [])
    print(f"소스: {len(sources)}개 / 키워드: {len(keywords)}개")

    all_items: list[dict] = []
    for source in sources:
        w = weights.get(source["name"], 1.0)
        items = fetch_feed(source, weight=w, keywords=keywords)
        all_items.extend(items)

    # 중복 제거 (link 기준)
    seen: set[str] = set()
    unique: list[dict] = []
    for item in all_items:
        key = item.get("link") or item.get("id")
        if key and key not in seen:
            seen.add(key)
            unique.append(item)

    # 중요도 내림차순 정렬
    unique.sort(key=lambda x: x.get("score", 0), reverse=True)
    print(f"\n수집 완료: {len(unique)}건 (중복 제거 후) — 상위 {TOP_N}건 AI 분석")

    # AI 인사이트
    insight = generate_insight(unique)

    # 텔레그램 전송
    send_telegram_briefing(unique, insight)

    # JSON 저장
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(unique),
        "sources": len(sources),
        "ai_insight": insight,
        "items": unique,
    }
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장: {OUTPUT_FILE}")

    push_to_api(unique)
    print("=== 완료 ===")


if __name__ == "__main__":
    main()
