#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "stock.db"
CAFE_ID = "30099599"
CAFE_BASE = "https://cafe.naver.com"
DEFAULT_BOARDS = [
    {
        "board_key": "popular",
        "board_name": "인기글",
        "url": f"{CAFE_BASE}/f-e/cafes/{CAFE_ID}/popular",
    },
    {
        "board_key": "menu_9",
        "board_name": "업종 및 기업 분석",
        "url": f"{CAFE_BASE}/f-e/cafes/{CAFE_ID}/menus/9?viewType=L",
    },
    {
        "board_key": "menu_34",
        "board_name": "업종 지표 활용법",
        "url": f"{CAFE_BASE}/f-e/cafes/{CAFE_ID}/menus/34?viewType=L",
    },
    {
        "board_key": "menu_11",
        "board_name": "지표 추가 & 아이디어 제안",
        "url": f"{CAFE_BASE}/f-e/cafes/{CAFE_ID}/menus/11?viewType=L",
    },
]

API_BOARD_MENUS = [
    {
        "board_key": "all",
        "board_name": "전체글",
        "menu_id": "0",
    },
    {
        "board_key": "menu_9",
        "board_name": "업종 및 기업 분석",
        "menu_id": "9",
    },
    {
        "board_key": "menu_34",
        "board_name": "업종 지표 활용법",
        "menu_id": "34",
    },
    {
        "board_key": "menu_11",
        "board_name": "지표 추가 & 아이디어 제안",
        "menu_id": "11",
    },
]

MENU_NAME_BY_ID = {item["menu_id"]: item["board_name"] for item in API_BOARD_MENUS}
MENU_KEY_BY_ID = {item["menu_id"]: item["board_key"] for item in API_BOARD_MENUS}


SECTOR_KEYWORDS = {
    "반도체": ["반도체", "메모리", "HBM", "디램", "DRAM", "낸드", "NAND", "PCB", "기판", "특수가스", "네온", "제논", "크립톤"],
    "전력기기": ["전력기기", "변압기", "배전반", "차단기", "전력선", "계전기"],
    "방산/항공": ["방산", "항공", "미사일", "레이더", "무인기", "전차", "KAI"],
    "건설/건자재": ["건설", "건자재", "착공", "건설기성", "건설수주", "H형강", "철근", "굴착기"],
    "정유/화학": ["정유", "윤활기유", "석유화학", "합성수지", "스프레드", "에폭시", "가성칼륨", "탄산칼륨"],
    "철강/비철": ["철강", "스테인리스", "알루미늄", "구리", "후판", "열연"],
    "자동차": ["자동차", "완성차", "부품", "타이어", "타이어코드", "중고차"],
    "소비재": ["식품", "주류", "맥주", "소주", "의류", "백화점", "호텔", "화장품"],
    "바이오/의료": ["의료기기", "진단", "바이오", "제약", "백신"],
    "게임/미디어": ["게임", "영화관", "IPTV", "광고", "미디어", "엔터"],
    "조선/기계": ["조선", "선박", "엔진", "피팅", "밸브", "공작기계", "로봇"],
}

INDICATOR_KEYWORDS = {
    "수출입": ["수출", "수입", "수출입", "무역수지", "HS", "단가"],
    "가격/판가": ["가격", "판가", "ASP", "스프레드", "원가", "마진"],
    "수주": ["수주", "수주잔고", "신규수주", "계약"],
    "실적": ["실적", "매출", "영업이익", "순이익", "OPM", "EPS", "PER"],
    "투자/증설": ["투자", "증설", "CAPA", "CapEx", "장기 투자"],
    "수급": ["외국인", "기관", "순매수", "거래대금", "거래량"],
    "매크로": ["금리", "환율", "유가", "착공", "기성액", "PMI", "BSI"],
}

SIGNAL_WORDS = {
    "positive": ["상승", "개선", "반등", "증가", "확대", "강세", "호황", "기회", "저평가", "싸다", "수혜", "회복"],
    "negative": ["하락", "감소", "둔화", "악화", "조정", "약세", "부담", "리스크", "감산", "침체"],
    "watch": ["관전", "전망", "기대", "확인", "점검", "키워드", "리뷰"],
}


@dataclass
class CafePost:
    cafe_id: str
    board_key: str
    board_name: str
    article_id: str
    title: str
    url: str
    author: str = ""
    published_at: str = ""
    excerpt: str = ""
    content_hash: str = ""


def now_kst() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cafe_signal_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cafe_id TEXT NOT NULL,
            board_key TEXT NOT NULL,
            board_name TEXT,
            article_id TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            author TEXT,
            published_at TEXT,
            excerpt TEXT,
            content_hash TEXT,
            collected_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(cafe_id, article_id)
        );

        CREATE TABLE IF NOT EXISTS cafe_signal_mentions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cafe_post_id INTEGER NOT NULL,
            mention_type TEXT NOT NULL,
            mention_key TEXT NOT NULL,
            mention_name TEXT NOT NULL,
            stock_code TEXT,
            stock_name TEXT,
            sector_name TEXT,
            indicator_name TEXT,
            signal_direction TEXT,
            confidence REAL DEFAULT 0.5,
            evidence TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(cafe_post_id, mention_type, mention_key)
        );

        CREATE TABLE IF NOT EXISTS cafe_signal_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_type TEXT NOT NULL,
            period_key TEXT NOT NULL,
            source_board_keys TEXT,
            posts_count INTEGER DEFAULT 0,
            stocks_count INTEGER DEFAULT 0,
            sectors_count INTEGER DEFAULT 0,
            indicators_count INTEGER DEFAULT 0,
            summary_json TEXT,
            generated_at TEXT NOT NULL,
            UNIQUE(run_type, period_key)
        );

        CREATE TABLE IF NOT EXISTS cafe_signal_post_bodies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cafe_post_id INTEGER NOT NULL,
            cafe_id TEXT NOT NULL,
            article_id TEXT NOT NULL,
            content_text TEXT,
            content_hash TEXT,
            fetched_at TEXT NOT NULL,
            UNIQUE(cafe_id, article_id)
        );

        CREATE INDEX IF NOT EXISTS idx_cafe_posts_collected ON cafe_signal_posts(collected_at);
        CREATE INDEX IF NOT EXISTS idx_cafe_mentions_type ON cafe_signal_mentions(mention_type, mention_key);
        CREATE INDEX IF NOT EXISTS idx_cafe_runs_period ON cafe_signal_runs(run_type, period_key);
        CREATE INDEX IF NOT EXISTS idx_cafe_bodies_article ON cafe_signal_post_bodies(cafe_id, article_id);
        """
    )
    conn.commit()


def load_dotenv_if_exists() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def request_session() -> requests.Session:
    load_dotenv_if_exists()
    cookie = os.getenv("NAVER_CAFE_COOKIE", "") or os.getenv("NAVER_COOKIE", "")
    if not cookie:
        cookie_path = ROOT / ".naver_cafe_cookie.tmp"
        if cookie_path.exists():
            cookie = cookie_path.read_text(encoding="utf-8", errors="ignore").strip()
    sess = requests.Session()
    sess.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": f"{CAFE_BASE}/indistore",
        }
    )
    if cookie:
        sess.headers["Cookie"] = cookie
    return sess


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def content_hash(title: str, text: str) -> str:
    return hashlib.sha256((title + "\n" + text).encode("utf-8", errors="ignore")).hexdigest()


def article_id_from_url(url: str) -> str:
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    for key in ("articleid", "articleId"):
        if params.get(key):
            return params[key][0]
    m = re.search(r"/articles/(\d+)", parsed.path)
    if m:
        return m.group(1)
    m = re.search(r"articleid=(\d+)", url)
    return m.group(1) if m else hashlib.sha1(url.encode()).hexdigest()[:16]


def canonical_article_url(url: str) -> str:
    abs_url = urljoin(CAFE_BASE, url)
    article_id = article_id_from_url(abs_url)
    return f"{CAFE_BASE}/ArticleRead.nhn?clubid={CAFE_ID}&articleid={article_id}"


def extract_post_links(html: str, board: dict) -> list[CafePost]:
    soup = BeautifulSoup(html, "html.parser")
    posts: dict[str, CafePost] = {}
    for a in soup.select("a[href]"):
        href = a.get("href") or ""
        text = normalize_space(a.get_text(" "))
        if not text or len(text) < 3:
            continue
        if "articleid=" not in href and "/articles/" not in href:
            continue
        article_id = article_id_from_url(href)
        if not article_id:
            continue
        if article_id not in posts:
            url = canonical_article_url(href)
            posts[article_id] = CafePost(
                cafe_id=CAFE_ID,
                board_key=board["board_key"],
                board_name=board["board_name"],
                article_id=article_id,
                title=text[:240],
                url=url,
            )
    return list(posts.values())


def kst_from_timestamp_ms(value) -> str:
    try:
        ts = int(value) / 1000
    except Exception:
        return ""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def post_from_api_item(item: dict, board: dict) -> CafePost | None:
    article_id = str(item.get("articleId") or "")
    title = normalize_space(item.get("subject") or item.get("title") or "")
    if not article_id or not title:
        return None
    writer = item.get("writerInfo") or {}
    summary = normalize_space(item.get("summary") or "")
    meta = []
    if item.get("readCount") is not None:
        meta.append(f"조회 {item.get('readCount')}")
    if item.get("commentCount") is not None:
        meta.append(f"댓글 {item.get('commentCount')}")
    if item.get("likeCount") is not None:
        meta.append(f"좋아요 {item.get('likeCount')}")
    excerpt = normalize_space(" ".join([summary, " / ".join(meta)]))
    item_menu_id = str(item.get("menuId") or board["menu_id"])
    board_key = MENU_KEY_BY_ID.get(item_menu_id, f"menu_{item_menu_id}")
    board_name = MENU_NAME_BY_ID.get(item_menu_id, board["board_name"])
    return CafePost(
        cafe_id=CAFE_ID,
        board_key=board_key,
        board_name=board_name,
        article_id=article_id,
        title=title[:240],
        url=f"{CAFE_BASE}/f-e/cafes/{CAFE_ID}/articles/{article_id}?boardtype=L&menuid={item_menu_id}&referrerAllArticles=false",
        author=normalize_space(writer.get("nickName") or writer.get("nickname") or "")[:80],
        published_at=kst_from_timestamp_ms(item.get("writeDateTimestamp")),
        excerpt=excerpt[:700],
        content_hash=content_hash(title, excerpt),
    )


def collect_posts_from_board_api(max_pages: int = 20, page_size: int = 50) -> list[CafePost]:
    sess = request_session()
    headers = {
        "Accept": "application/json",
        "Origin": CAFE_BASE,
        "Referer": f"{CAFE_BASE}/f-e/cafes/{CAFE_ID}/menus/9",
        "X-Cafe-Product": "pc",
    }
    all_posts: list[CafePost] = []
    for board in API_BOARD_MENUS:
        empty_pages = 0
        for page in range(1, max_pages + 1):
            url = f"https://apis.naver.com/cafe-web/cafe-boardlist-api/v1/cafes/{CAFE_ID}/menus/{board['menu_id']}/articles"
            params = {"page": page, "pageSize": page_size, "viewType": "L"}
            try:
                resp = sess.get(url, params=params, headers={**headers, "Referer": f"{CAFE_BASE}/f-e/cafes/{CAFE_ID}/menus/{board['menu_id']}"}, timeout=20)
                resp.raise_for_status()
                payload = resp.json()
            except Exception as exc:
                print(f"[WARN] api fetch failed {board['board_key']} page={page}: {exc}", file=sys.stderr)
                break
            result = payload.get("result") or {}
            raw_items = result.get("articleList") or []
            posts = []
            for raw in raw_items:
                if raw.get("type") != "ARTICLE":
                    continue
                post = post_from_api_item(raw.get("item") or {}, board)
                if post:
                    posts.append(post)
            all_posts.extend(posts)
            if not posts:
                empty_pages += 1
            else:
                empty_pages = 0
            page_info = result.get("pageInfo") or {}
            if empty_pages >= 2:
                break
            if not page_info.get("visibleNextButton") and page >= int(page_info.get("lastNavigationPageNumber") or page):
                break
    dedup: dict[tuple[str, str], CafePost] = {}
    for post in all_posts:
        dedup[(post.cafe_id, post.article_id)] = post
    return list(dedup.values())


def fetch_article_detail(sess: requests.Session, post: CafePost) -> CafePost:
    try:
        resp = sess.get(post.url, timeout=20)
        resp.raise_for_status()
    except Exception:
        post.content_hash = content_hash(post.title, post.excerpt)
        return post
    soup = BeautifulSoup(resp.text, "html.parser")
    title = normalize_space(
        (soup.select_one(".title_text") or soup.select_one("h3") or soup.select_one("title") or soup).get_text(" ")
    )
    if title and "네이버 카페" not in title:
        post.title = title[:240]
    author_el = soup.select_one(".nickname, .nick, .writer")
    if author_el:
        post.author = normalize_space(author_el.get_text(" "))[:80]
    date_el = soup.select_one(".date, .ArticleTool .date")
    if date_el:
        post.published_at = normalize_space(date_el.get_text(" "))[:40]
    body_el = soup.select_one(".se-main-container") or soup.select_one("#tbody") or soup.select_one(".ContentRenderer")
    body = normalize_space(body_el.get_text(" ")) if body_el else normalize_space(soup.get_text(" "))
    post.excerpt = body[:700]
    post.content_hash = content_hash(post.title, body)
    return post


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return normalize_space(soup.get_text(" "))


def fetch_article_body_api(sess: requests.Session, article_id: str) -> tuple[str, str] | None:
    url = f"https://apis.naver.com/cafe-web/cafe-articleapi/v1/cafes/{CAFE_ID}/articles/{article_id}"
    headers = {
        "Accept": "application/json",
        "Origin": CAFE_BASE,
        "Referer": f"{CAFE_BASE}/f-e/cafes/{CAFE_ID}/articles/{article_id}",
        "X-Cafe-Product": "pc",
    }
    try:
        resp = sess.get(url, headers=headers, timeout=25)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        print(f"[WARN] body fetch failed article={article_id}: {exc}", file=sys.stderr)
        return None
    article = payload.get("article") or {}
    text = html_to_text(article.get("content") or "")
    if not text:
        return None
    return text, content_hash(article.get("subject") or "", text)


def fetch_and_store_bodies(conn: sqlite3.Connection, limit: int = 0, overwrite: bool = False) -> dict:
    sess = request_session()
    where = ""
    if not overwrite:
        where = """
        WHERE NOT EXISTS (
            SELECT 1 FROM cafe_signal_post_bodies b
            WHERE b.cafe_id=p.cafe_id AND b.article_id=p.article_id
              AND b.content_text IS NOT NULL AND LENGTH(b.content_text) > 20
        )
        """
    sql = f"""
        SELECT p.*
        FROM cafe_signal_posts p
        {where}
        ORDER BY CAST(p.article_id AS INTEGER) DESC
    """
    if limit and limit > 0:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql).fetchall()
    fetched = 0
    failed = 0
    ts = now_kst()
    for idx, post in enumerate(rows, 1):
        result = fetch_article_body_api(sess, str(post["article_id"]))
        if not result:
            failed += 1
            continue
        text, chash = result
        conn.execute(
            """
            INSERT INTO cafe_signal_post_bodies
            (cafe_post_id, cafe_id, article_id, content_text, content_hash, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(cafe_id, article_id) DO UPDATE SET
                cafe_post_id=excluded.cafe_post_id,
                content_text=excluded.content_text,
                content_hash=excluded.content_hash,
                fetched_at=excluded.fetched_at
            """,
            (post["id"], post["cafe_id"], post["article_id"], text, chash, ts),
        )
        conn.execute(
            """
            UPDATE cafe_signal_posts
            SET excerpt=?, content_hash=?, updated_at=?
            WHERE id=?
            """,
            (text[:700], chash, ts, post["id"]),
        )
        fetched += 1
        if idx % 100 == 0:
            conn.commit()
            print(f"[INFO] bodies fetched {idx}/{len(rows)} ok={fetched} failed={failed}", file=sys.stderr)
    conn.commit()
    return {"targets": len(rows), "fetched": fetched, "failed": failed}


def collect_posts(max_pages: int = 2, include_detail: bool = True) -> list[CafePost]:
    api_posts = collect_posts_from_board_api(max_pages=max_pages)
    if api_posts:
        return api_posts

    sess = request_session()
    all_posts: list[CafePost] = []
    for board in DEFAULT_BOARDS:
        for page in range(1, max_pages + 1):
            url = board["url"]
            sep = "&" if "?" in url else "?"
            page_url = f"{url}{sep}page={page}"
            try:
                resp = sess.get(page_url, timeout=20)
                resp.raise_for_status()
            except Exception as exc:
                print(f"[WARN] fetch failed {board['board_key']} page={page}: {exc}", file=sys.stderr)
                continue
            posts = extract_post_links(resp.text, board)
            for post in posts:
                all_posts.append(fetch_article_detail(sess, post) if include_detail else post)
    dedup: dict[tuple[str, str], CafePost] = {}
    for post in all_posts:
        dedup[(post.cafe_id, post.article_id)] = post
    return list(dedup.values())


def upsert_posts(conn: sqlite3.Connection, posts: Iterable[CafePost]) -> list[int]:
    ids: list[int] = []
    ts = now_kst()
    for post in posts:
        if not post.content_hash:
            post.content_hash = content_hash(post.title, post.excerpt)
        conn.execute(
            """
            INSERT INTO cafe_signal_posts
            (cafe_id, board_key, board_name, article_id, title, url, author, published_at, excerpt, content_hash, collected_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cafe_id, article_id) DO UPDATE SET
                board_key=excluded.board_key,
                board_name=excluded.board_name,
                title=excluded.title,
                url=excluded.url,
                author=excluded.author,
                published_at=excluded.published_at,
                excerpt=excluded.excerpt,
                content_hash=excluded.content_hash,
                updated_at=excluded.updated_at
            """,
            (
                post.cafe_id,
                post.board_key,
                post.board_name,
                post.article_id,
                post.title,
                post.url,
                post.author,
                post.published_at,
                post.excerpt,
                post.content_hash,
                ts,
                ts,
            ),
        )
        row = conn.execute(
            "SELECT id FROM cafe_signal_posts WHERE cafe_id=? AND article_id=?",
            (post.cafe_id, post.article_id),
        ).fetchone()
        if row:
            ids.append(int(row[0]))
    conn.commit()
    return ids


def load_stock_names(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute(
        """
        SELECT stock_code, stock_name, market, sector_large, sector_mid
        FROM stock_universe
        WHERE stock_type IS NULL OR stock_type NOT LIKE '%ETF%'
        """
    ).fetchall()
    names: dict[str, dict] = {}
    for r in rows:
        name = r["stock_name"]
        if not name or len(name) < 2:
            continue
        names[name] = dict(r)
    return names


def classify_direction(text: str) -> tuple[str, float]:
    scores = {}
    for direction, words in SIGNAL_WORDS.items():
        scores[direction] = sum(1 for w in words if w.lower() in text.lower())
    if scores["positive"] > scores["negative"] and scores["positive"] >= 1:
        return "positive", min(0.95, 0.55 + scores["positive"] * 0.08)
    if scores["negative"] > scores["positive"] and scores["negative"] >= 1:
        return "negative", min(0.9, 0.55 + scores["negative"] * 0.08)
    if scores["watch"] >= 1:
        return "watch", min(0.8, 0.5 + scores["watch"] * 0.06)
    return "neutral", 0.45


def build_stock_matcher(stock_names: dict[str, dict]) -> re.Pattern | None:
    names = [name for name in stock_names if name and len(name) >= 2]
    names.sort(key=len, reverse=True)
    if not names:
        return None
    return re.compile(r"(?<![0-9A-Za-z가-힣])(" + "|".join(re.escape(name) for name in names) + r")(?![0-9A-Za-z가-힣])")


def extract_mentions_for_post(post: sqlite3.Row, stock_names: dict[str, dict], stock_pattern: re.Pattern | None = None) -> list[dict]:
    body_text = ""
    try:
        body_text = post["body_text"] or ""
    except Exception:
        body_text = ""
    text = normalize_space(f"{post['title']} {body_text or post['excerpt'] or ''}")
    direction, base_conf = classify_direction(text)
    mentions: list[dict] = []

    for sector_name, words in SECTOR_KEYWORDS.items():
        hits = [w for w in words if w.lower() in text.lower()]
        if not hits:
            continue
        mentions.append(
            {
                "mention_type": "sector",
                "mention_key": sector_name,
                "mention_name": sector_name,
                "sector_name": sector_name,
                "indicator_name": "",
                "signal_direction": direction,
                "confidence": min(0.95, base_conf + len(hits) * 0.05),
                "evidence": ", ".join(hits[:6]),
            }
        )

    for indicator_name, words in INDICATOR_KEYWORDS.items():
        hits = [w for w in words if w.lower() in text.lower()]
        if not hits:
            continue
        mentions.append(
            {
                "mention_type": "indicator",
                "mention_key": indicator_name,
                "mention_name": indicator_name,
                "sector_name": "",
                "indicator_name": indicator_name,
                "signal_direction": direction,
                "confidence": min(0.9, base_conf + len(hits) * 0.04),
                "evidence": ", ".join(hits[:6]),
            }
        )

    if stock_pattern:
        seen_codes: set[str] = set()
        for match in stock_pattern.finditer(text):
            stock_name = match.group(1)
            meta = stock_names.get(stock_name)
            if not meta or meta["stock_code"] in seen_codes:
                continue
            seen_codes.add(meta["stock_code"])
            mentions.append(
                {
                    "mention_type": "stock",
                    "mention_key": meta["stock_code"],
                    "mention_name": stock_name,
                    "stock_code": meta["stock_code"],
                    "stock_name": stock_name,
                    "sector_name": meta.get("sector_large") or "",
                    "indicator_name": "",
                    "signal_direction": direction,
                    "confidence": min(0.92, base_conf + 0.18),
                    "evidence": stock_name,
                }
            )
    return mentions


def rebuild_mentions(conn: sqlite3.Connection, days: int = 3700) -> dict:
    stock_names = load_stock_names(conn)
    stock_pattern = build_stock_matcher(stock_names)
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    posts = conn.execute(
        """
        SELECT p.*, b.content_text AS body_text
        FROM cafe_signal_posts p
        LEFT JOIN cafe_signal_post_bodies b
          ON b.cafe_id=p.cafe_id AND b.article_id=p.article_id
        WHERE p.collected_at >= ?
        ORDER BY p.collected_at DESC
        """,
        (cutoff,),
    ).fetchall()
    inserted = 0
    ts = now_kst()
    for post in posts:
        conn.execute("DELETE FROM cafe_signal_mentions WHERE cafe_post_id=?", (post["id"],))
        for m in extract_mentions_for_post(post, stock_names, stock_pattern):
            conn.execute(
                """
                INSERT OR IGNORE INTO cafe_signal_mentions
                (cafe_post_id, mention_type, mention_key, mention_name, stock_code, stock_name,
                 sector_name, indicator_name, signal_direction, confidence, evidence, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    post["id"],
                    m.get("mention_type", ""),
                    m.get("mention_key", ""),
                    m.get("mention_name", ""),
                    m.get("stock_code", ""),
                    m.get("stock_name", ""),
                    m.get("sector_name", ""),
                    m.get("indicator_name", ""),
                    m.get("signal_direction", "neutral"),
                    m.get("confidence", 0.5),
                    m.get("evidence", ""),
                    ts,
                ),
            )
            inserted += 1
    conn.commit()
    return {"posts": len(posts), "mentions": inserted}


def period_key(run_type: str) -> str:
    today = datetime.now().date()
    if run_type == "monthly":
        return today.strftime("%Y-%m")
    iso = today.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def build_run(conn: sqlite3.Connection, run_type: str) -> dict:
    days = 35 if run_type == "monthly" else 10
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    posts = conn.execute(
        "SELECT COUNT(*) FROM cafe_signal_posts WHERE collected_at>=?",
        (cutoff,),
    ).fetchone()[0]
    rows = conn.execute(
        """
        SELECT m.*, p.title, p.url, p.board_name, p.collected_at
        FROM cafe_signal_mentions m
        JOIN cafe_signal_posts p ON p.id=m.cafe_post_id
        WHERE p.collected_at>=?
        ORDER BY m.confidence DESC, p.collected_at DESC
        """,
        (cutoff,),
    ).fetchall()

    counters = {
        "stock": Counter(),
        "sector": Counter(),
        "indicator": Counter(),
    }
    examples: dict[str, list[dict]] = defaultdict(list)
    direction_score = {"positive": 1.0, "watch": 0.35, "neutral": 0.0, "negative": -1.0}

    for r in rows:
        mtype = r["mention_type"]
        key = r["mention_key"]
        if mtype not in counters or not key:
            continue
        weight = max(0.1, float(r["confidence"] or 0.5)) + direction_score.get(r["signal_direction"], 0) * 0.15
        counters[mtype][key] += weight
        if len(examples[f"{mtype}:{key}"]) < 3:
            examples[f"{mtype}:{key}"].append(
                {
                    "title": r["title"],
                    "url": r["url"],
                    "board_name": r["board_name"],
                    "direction": r["signal_direction"],
                    "evidence": r["evidence"],
                    "confidence": r["confidence"],
                    "collected_at": r["collected_at"],
                }
            )

    def top_items(mtype: str, limit: int = 20) -> list[dict]:
        items = []
        for key, score in counters[mtype].most_common(limit):
            sample = examples.get(f"{mtype}:{key}", [])
            name = key
            if sample:
                row = next((r for r in rows if r["mention_type"] == mtype and r["mention_key"] == key), None)
                if row:
                    name = row["mention_name"]
            items.append({"key": key, "name": name, "score": round(score, 3), "examples": sample})
        return items

    summary = {
        "run_type": run_type,
        "period_key": period_key(run_type),
        "lookback_days": days,
        "top_stocks": top_items("stock", 30),
        "top_sectors": top_items("sector", 20),
        "top_indicators": top_items("indicator", 20),
        "generated_at": now_kst(),
    }
    conn.execute(
        """
        INSERT INTO cafe_signal_runs
        (run_type, period_key, source_board_keys, posts_count, stocks_count, sectors_count, indicators_count, summary_json, generated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_type, period_key) DO UPDATE SET
            source_board_keys=excluded.source_board_keys,
            posts_count=excluded.posts_count,
            stocks_count=excluded.stocks_count,
            sectors_count=excluded.sectors_count,
            indicators_count=excluded.indicators_count,
            summary_json=excluded.summary_json,
            generated_at=excluded.generated_at
        """,
        (
            run_type,
            summary["period_key"],
            ",".join(b["board_key"] for b in DEFAULT_BOARDS),
            posts,
            len(summary["top_stocks"]),
            len(summary["top_sectors"]),
            len(summary["top_indicators"]),
            json.dumps(summary, ensure_ascii=False),
            summary["generated_at"],
        ),
    )
    conn.commit()
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Naver Cafe signal collector for stock_dashboard")
    ap.add_argument("--init-only", action="store_true")
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--max-pages", type=int, default=2)
    ap.add_argument("--no-detail", action="store_true")
    ap.add_argument("--rebuild-mentions", action="store_true")
    ap.add_argument("--fetch-bodies", action="store_true")
    ap.add_argument("--body-limit", type=int, default=0)
    ap.add_argument("--overwrite-bodies", action="store_true")
    ap.add_argument("--run-type", choices=["weekly", "monthly"], default="weekly")
    ap.add_argument("--days", type=int, default=3700)
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    init_db(conn)

    result = {"status": "ok", "db": str(DB_PATH)}
    if args.collect:
        posts = collect_posts(max_pages=args.max_pages, include_detail=not args.no_detail)
        ids = upsert_posts(conn, posts)
        result["collected_posts"] = len(ids)
    if args.fetch_bodies:
        result["body_fetch"] = fetch_and_store_bodies(conn, limit=args.body_limit, overwrite=args.overwrite_bodies)
    if args.rebuild_mentions or args.collect:
        result["mention_rebuild"] = rebuild_mentions(conn, days=args.days)
    if not args.init_only:
        result["run"] = build_run(conn, args.run_type)
    conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
