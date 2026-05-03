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
from ticker_utils import ticker_mapper
from notifier import send as send_telegram

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = "/Applications/stock_dashboard/stock.db"

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

def parse_blog_post_with_ai(title, content, image_urls):
    """AI를 사용하여 블로그 내용 파싱"""
    api_key = getattr(config, "OPENAI_API_KEY", "")
    if not api_key:
        return None, "OpenAI API Key is missing"

    try:
        import openai
        client = openai.OpenAI(api_key=api_key)
        
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
    """블로그 카테고리 목록 스크래핑 (돈의흐름 팔로잉)"""
    url = "https://blog.naver.com/PostList.naver?blogId=going_tothe_moon&categoryNo=49"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        res = requests.get(url, headers=headers)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        
        posts = []
        items = soup.select(".list_title_text a")
        if not items:
            items = soup.select(".post_title a")
            
        for item in items:
            title = item.text.strip()
            link = "https://blog.naver.com" + item["href"]
            post_id = link.split("logNo=")[1].split("&")[0] if "logNo=" in link else ""
            posts.append({"title": title, "link": link, "post_id": post_id})
            
        return posts
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

def run_parser():
    """메인 파서 실행 로직"""
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
        if cursor.fetchone():
            continue
            
        logger.info(f"Processing new post: {p['title']}")
        text, images, post_date = get_post_detail(p["link"])
        
        ai_result, error = parse_blog_post_with_ai(p["title"], text, images)
        if error:
            logger.error(f"AI parsing failed for {p['title']}: {error}")
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
