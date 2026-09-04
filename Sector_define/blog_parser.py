import requests
from bs4 import BeautifulSoup
import sqlite3
import json
import logging
from datetime import datetime
import sys
import os

# 부모 디렉토리의 모듈을 가져오기 위해 path 추가
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from services.gemini_openai_compat import OpenAI
from ticker_utils import ticker_mapper
from notifier import send as send_telegram

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"

def get_current_price(stock_code):
    """stock.db에서 최신 종가 가져오기"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 1",
            (stock_code,)
        )
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception as e:
        logger.error(f"Error fetching price for {stock_code}: {e}")
        return None

_stock_name_cache: list = []
_stock_name_cache_at: float = 0.0

def _load_stock_names() -> list:
    """stock_universe에서 종목명 목록 로드 (길이 내림차순 — 부분매칭 방지)."""
    global _stock_name_cache, _stock_name_cache_at
    import time
    if _stock_name_cache and (time.time() - _stock_name_cache_at) < 3600:
        return _stock_name_cache
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT stock_name FROM stock_universe WHERE market IN ('유가증권','KOSPI','코스닥','KOSDAQ')"
        ).fetchall()
        conn.close()
        names = sorted([r[0] for r in rows if r[0]], key=len, reverse=True)
        _stock_name_cache = names
        _stock_name_cache_at = time.time()
        return names
    except Exception as e:
        logger.error(f"[blog_parser] stock_names 로드 실패: {e}")
        return []


def parse_blog_post_local(title: str, content: str) -> tuple:
    """로컬 종목명 매칭으로 블로그 내용 파싱 (OpenAI 불필요).

    처리 방식:
      - ​ (ZWSP) 로 분리된 단락 처리
      - ■/□/◆/▶/-로 시작하는 카테고리 헤더 감지
      - stock_universe 종목명 최장 매칭

    Returns:
        (result_dict, None) on success  — result_dict: {"summary": str, "data": [...]}
        (None, error_msg) on failure
    """
    import re

    stock_names = _load_stock_names()
    if not stock_names:
        return None, "stock_universe 데이터 없음"

    # ZWSP(​)·개행 모두로 단락 분리 후, 각 단락 내 '-카테고리(설명)stocks' 세그먼트 분리
    raw_paragraphs = re.split(r'[​\n]+', content)

    # 각 단락을 다시 '-카테고리명(...)' 경계로 분리
    seg_pattern = re.compile(r'(?=-([\w가-힣· ]{2,20})[\(（])')
    paragraphs: list[str] = []
    for p in raw_paragraphs:
        parts = seg_pattern.split(p)
        if len(parts) > 1:
            # split 결과: [앞텍스트, cat1, rest1, cat2, rest2, ...]
            paragraphs.append(parts[0])
            i = 1
            while i + 1 < len(parts):
                paragraphs.append(f"-{parts[i]}({parts[i+1]}")
                i += 2
        else:
            paragraphs.append(p)

    category_stocks: dict = {}   # {category: [stock_name, ...]}
    current_cat = title          # 기본 카테고리 = 포스트 제목

    strong_header = re.compile(r'^[■□◆▶◇●【\[]+\s*(.+)')
    dash_header   = re.compile(r'^-([^,\s(][^,(]{1,20})\s*[\(（]([^)）]{0,20})[\)）]')

    for para in paragraphs:
        stripped = para.strip()
        if not stripped or len(stripped) < 2:
            continue

        # 강한 헤더 (■ 등)
        m = strong_header.match(stripped)
        if m:
            cat_text = re.sub(r'[】\]]+.*$', '', m.group(1)).strip()
            if 2 <= len(cat_text) <= 40:
                current_cat = cat_text
            continue

        # 대시 헤더 (-카테고리명(설명)) — 카테고리 이름만 추출
        m2 = dash_header.match(stripped)
        if m2:
            cat_text = m2.group(1).strip()
            if 2 <= len(cat_text) <= 25:
                current_cat = cat_text
            # 같은 단락에 종목명도 있으니 fall-through

        # 종목명 탐지 (longest-match; 이미 찾은 건 공백으로 치환)
        matched = []
        remaining = stripped
        for name in stock_names:
            if name in remaining:
                matched.append(name)
                remaining = remaining.replace(name, " ", 1)

        for m_name in matched:
            category_stocks.setdefault(current_cat, [])
            if m_name not in category_stocks[current_cat]:
                category_stocks[current_cat].append(m_name)

    if not category_stocks:
        return None, "매칭된 종목 없음"

    data = [{"category": cat, "stocks": stocks}
            for cat, stocks in category_stocks.items() if stocks]
    total = sum(len(d["stocks"]) for d in data)
    result = {
        "summary": f"{title} — 로컬매칭 {total}개 종목 추출",
        "data": data,
    }
    return result, None


def parse_blog_post_with_ai(title, content, image_urls):
    """AI를 사용하여 블로그 내용 파싱"""
    api_key = getattr(config, "GEMINI_API_KEY", "")
    if not api_key:
        return None, "Gemini API Key is missing"

    try:
        client = OpenAI()
        
        messages = [
            {
                "role": "system",
                "content": (
                    "당신은 주식 분석 전문가입니다. 블로그 포스트의 내용과 이미지를 분석하여 "
                    "섹터(Level 1)와 해당 섹터에 속한 종목들(Level 2)을 추출해야 합니다. "
                    "결과는 반드시 JSON 형식으로 반환하세요. "
                    "형식: {\"summary\": \"...\", \"data\": [{\"category\": \"섹터명\", \"stocks\": [\"종목1\", \"종목2\"]}]} "
                    "종목명은 한국 주식일 경우 정확한 명칭을 사용하세요."
                )
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"제목: {title}\n내용: {content[:2000]}"}
                ]
            }
        ]
        
        for url in image_urls[:3]:
            messages[1]["content"].append({"type": "image_url", "image_url": {"url": url}})

        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            response_format={"type": "json_object"},
            max_tokens=1000,
            temperature=0
        )
        
        return json.loads(res.choices[0].message.content), None
    except Exception as e:
        logger.error(f"AI Parsing Error: {e}")
        return None, str(e)

def scrape_blog_list():
    """블로그 카테고리 목록 스크래핑 (돈의흐름 팔로잉 — categoryNo=7)"""
    import re as _re
    blog_id = "going_tothe_moon"
    cat_no  = "7"
    url = f"https://blog.naver.com/PostList.naver?blogId={blog_id}&categoryNo={cat_no}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        res = requests.get(url, headers=headers, timeout=10)
        res.raise_for_status()

        # logNo 추출 → 포스트 URL 구성
        log_nos = list(dict.fromkeys(_re.findall(r"logNo=(\d{10,})", res.text)))
        if not log_nos:
            logger.warning("[blog_parser] logNo 패턴 미발견 — 페이지 구조 변경 의심")
            return []

        posts = []
        for log_no in log_nos[:30]:
            mobile_url = f"https://m.blog.naver.com/{blog_id}/{log_no}"
            # 제목은 별도 fetch 없이 포스트 상세에서 가져옴 (여기선 logNo만 수집)
            posts.append({"title": "", "link": mobile_url, "post_id": log_no})

        # 각 포스트 제목 채우기 (최신 10개만 — 신규 감지용)
        seen_in_db: set = set()
        try:
            _conn = sqlite3.connect(DB_PATH)
            rows = _conn.execute("SELECT blog_url FROM sector_posts").fetchall()
            _conn.close()
            seen_in_db = {r[0] for r in rows}
        except Exception:
            pass

        result = []
        for p in posts:
            if p["link"] not in seen_in_db:
                # 신규 포스트 — 제목 fetch
                text, _, post_date = get_post_detail(p["link"])
                # 제목은 HTML <title> 또는 첫 줄로 추정
                import re as _re2
                title_m = _re2.search(r'<title[^>]*>([^<]+)</title>', text[:500] if len(text) < 500 else "")
                title = p["post_id"]  # fallback
                lines = [l.strip() for l in text.split("\n") if l.strip() and len(l.strip()) > 4 and len(l.strip()) < 60]
                if len(lines) >= 2:
                    title = lines[1]  # 두 번째 줄이 보통 제목
                p["title"] = title
                result.append(p)
            else:
                p["title"] = p["post_id"]
                result.append(p)

        return result
    except Exception as e:
        logger.error(f"Scraping Error: {e}")
        return []

def get_post_detail(post_link):
    """포스트 상세 내용 및 이미지 URL 추출"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        res = requests.get(post_link, headers=headers)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        
        main_frame = soup.select_one("#mainFrame")
        if main_frame:
            frame_url = "https://blog.naver.com" + main_frame["src"]
            res = requests.get(frame_url, headers=headers)
            soup = BeautifulSoup(res.text, "html.parser")
        
        content = soup.select_one(".se-viewer") or soup.select_one("#postViewArea")
        text = content.text.strip() if content else ""
        
        images = []
        img_tags = soup.select(".se-image-resource") or soup.select(".se-main-container img")
        for img in img_tags:
            src = img.get("src") or img.get("data-lazy-src")
            if src and "https" in src:
                images.append(src)
                
        date_tag = soup.select_one(".se_publishDate") or soup.select_one(".date")
        post_date = date_tag.text.strip() if date_tag else datetime.now().strftime("%Y-%m-%d")
        
        return text, images, post_date
    except Exception as e:
        logger.error(f"Detail Fetch Error: {e}")
        return "", [], ""

def _parse_post(title: str, content: str, image_urls: list) -> tuple:
    """OpenAI 우선 → 없으면 로컬 매칭으로 폴백."""
    api_key = getattr(config, "GEMINI_API_KEY", "")
    if api_key:
        result, err = parse_blog_post_with_ai(title, content, image_urls)
        if result:
            return result, None
        logger.warning(f"[blog_parser] AI 파싱 실패 ({err}), 로컬 매칭으로 폴백")
    return parse_blog_post_local(title, content)


def run_parser(reprocess_empty: bool = True):
    """메인 파서 실행 로직.
    reprocess_empty=True: sector_stocks가 없는 기존 포스트도 재파싱.
    OpenAI 키 있으면 AI 파싱, 없으면 로컬 종목명 매칭 폴백.
    """

    logger.info("Starting Blog Parser...")
    posts = scrape_blog_list()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 테이블 생성 보장
    cursor.execute("CREATE TABLE IF NOT EXISTS sector_posts (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, blog_url TEXT NOT NULL, post_date TEXT NOT NULL, ai_summary TEXT, telegram_sent INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    cursor.execute("CREATE TABLE IF NOT EXISTS sector_stocks (id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER, category TEXT, stock_name TEXT, stock_code TEXT, ref_price REAL, memo TEXT, FOREIGN KEY(post_id) REFERENCES sector_posts(id))")

    new_post_count = 0

    for p in posts:
        cursor.execute("SELECT id FROM sector_posts WHERE blog_url=?", (p["link"],))
        existing = cursor.fetchone()
        if existing:
            if not reprocess_empty:
                continue
            # 기존 포스트인데 종목이 없으면 재파싱
            post_db_id = existing[0]
            cursor.execute("SELECT COUNT(*) FROM sector_stocks WHERE post_id=?", (post_db_id,))
            stock_count = cursor.fetchone()[0]
            if stock_count > 0:
                continue  # 종목 있으면 스킵
            logger.info(f"Re-parsing (no stocks): {p['title']}")
            text, images, _ = get_post_detail(p["link"])
            ai_result, error = _parse_post(p["title"], text, images)
            if error or not ai_result:
                logger.error(f"Re-parse failed: {p['title']}: {error}")
                continue
            # 요약 업데이트
            cursor.execute("UPDATE sector_posts SET ai_summary=? WHERE id=?",
                           (ai_result.get("summary", ""), post_db_id))
            # 종목 삽입
            for entry in ai_result.get("data", []):
                category = entry.get("category")
                for s_name in entry.get("stocks", []):
                    code = ticker_mapper.get_code(s_name) if hasattr(ticker_mapper, 'get_code') else None
                    price = get_current_price(code) if code else None
                    cursor.execute("INSERT INTO sector_stocks (post_id, category, stock_name, stock_code, ref_price) VALUES (?,?,?,?,?)",
                                   (post_db_id, category, s_name, code, price))
            conn.commit()
            new_post_count += 1
            continue
            
        logger.info(f"Processing new post: {p['title']}")
        text, images, post_date = get_post_detail(p["link"])

        ai_result, error = _parse_post(p["title"], text, images)
        if error or not ai_result:
            logger.error(f"Parsing failed for {p['title']}: {error}")
            continue
            
        cursor.execute(
            "INSERT INTO sector_posts (title, blog_url, post_date, ai_summary) VALUES (?, ?, ?, ?)",
            (p["title"], p["link"], post_date, ai_result.get("summary", ""))
        )
        post_db_id = cursor.lastrowid
        
        telegram_msg_parts = [f"🚀 [신규 섹터 분석] {p['title']}", f"\n{ai_result.get('summary', '')}\n", "📊 주요 종목:"]
        
        for entry in ai_result.get("data", []):
            category = entry.get("category")
            stocks = entry.get("stocks", [])
            
            category_msg = f"\n🔹 {category}:"
            stock_links = []
            
            for s_name in stocks:
                code = ticker_mapper.get_code(s_name)
                price = get_current_price(code) if code else None
                
                cursor.execute(
                    "INSERT INTO sector_stocks (post_id, category, stock_name, stock_code, ref_price) VALUES (?, ?, ?, ?, ?)",
                    (post_db_id, category, s_name, code, price)
                )
                
                price_str = f"({price:,.0f}원)" if price else ""
                stock_links.append(f"{s_name}{price_str}")
            
            telegram_msg_parts.append(category_msg + " " + ", ".join(stock_links))
            
        conn.commit()
        new_post_count += 1
        
        telegram_msg = "\n".join(telegram_msg_parts)
        send_telegram(telegram_msg, key=f"sector_post_{post_db_id}")
        
        cursor.execute("UPDATE sector_posts SET telegram_sent=1 WHERE id=?", (post_db_id,))
        conn.commit()
        
    conn.close()
    logger.info(f"Parser finished. {new_post_count} new posts processed.")

if __name__ == "__main__":
    run_parser()
