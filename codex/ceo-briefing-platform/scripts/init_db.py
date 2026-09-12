from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "ceo_briefing.db"


SCHEMA = """
PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS page_permissions;
DROP TABLE IF EXISTS calendar_events;
DROP TABLE IF EXISTS update_requests;
DROP TABLE IF EXISTS feed_items;
DROP TABLE IF EXISTS rss_sources;
DROP TABLE IF EXISTS app_settings;
DROP TABLE IF EXISTS calendar_integrations;
DROP TABLE IF EXISTS pages;

CREATE TABLE pages (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    page_type TEXT NOT NULL
);

CREATE TABLE page_permissions (
    page_id TEXT NOT NULL,
    role TEXT NOT NULL,
    PRIMARY KEY (page_id, role),
    FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE CASCADE
);

CREATE TABLE calendar_events (
    id TEXT PRIMARY KEY,
    page_id TEXT NOT NULL,
    event_date TEXT NOT NULL,
    event_time TEXT NOT NULL,
    title TEXT NOT NULL,
    place TEXT NOT NULL,
    owner TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE update_requests (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    requester TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE feed_items (
    id TEXT PRIMARY KEY,
    feed_type TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    link TEXT NOT NULL,
    source TEXT NOT NULL,
    article_published_at TEXT,
    article_publisher TEXT NOT NULL DEFAULT '',
    article_category TEXT NOT NULL DEFAULT 'reference',
    category_manual INTEGER NOT NULL DEFAULT 0,
    selected INTEGER NOT NULL DEFAULT 0,
    published INTEGER NOT NULL DEFAULT 0,
    published_at TEXT
);

CREATE TABLE rss_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_type TEXT NOT NULL,
    name TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE calendar_integrations (
    page_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    calendar_id TEXT NOT NULL,
    calendar_name TEXT NOT NULL,
    account_email TEXT NOT NULL,
    sync_enabled INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'not_connected',
    last_synced_at TEXT NOT NULL DEFAULT ''
);
"""


PAGES = [
    ("page1", "CEO 일정", "CEO calendar and key meetings", "calendar"),
    ("page2", "회사 주요 일정", "Company calendar and approval flow", "calendar"),
    ("page3", "주요 기사", "Article queue and parsing settings", "feed"),
    ("page4", "경쟁사 동향", "Competitor RSS sources and article queue", "feed"),
    ("page5", "캘린더 설정", "Google Calendar connections and write access", "calendar_settings"),
    ("page6", "OpenAI 설정", "API key and model settings for RSS summary", "openai_settings"),
    ("page8", "파싱 설정", "RSS and Naver parsing settings", "rss_settings"),
]

PERMISSIONS = [
    ("page1", "admin"),
    ("page2", "admin"),
    ("page3", "admin"),
    ("page5", "admin"),
    ("page6", "admin"),
    ("page8", "admin"),
]

CALENDAR_EVENTS = [
    ("ceo-1", "page1", "2026-04-07", "08:30", "임원 주간 미팅", "본사 18층", "비서실", "확정"),
    ("ceo-2", "page1", "2026-04-07", "11:00", "주요 사업 리뷰", "화상회의", "전략팀", "조정 중"),
    ("ceo-3", "page1", "2026-04-07", "16:00", "대외 미팅", "서울 사무실", "대외협력", "확정"),
    ("corp-1", "page2", "2026-04-07", "09:00", "전사 타운홀", "대강당", "인사팀", "사내 공지"),
    ("corp-2", "page2", "2026-04-07", "14:00", "감사위원회", "이사회실", "재경팀", "확인 완료"),
    ("corp-3", "page2", "2026-04-07", "17:30", "분기 실적 점검", "본사 12층", "IR", "수정 요청 대기"),
]

UPDATE_REQUESTS = [
    ("req-101", "분기 실적 점검 시간을 17:30에서 18:00으로 변경 요청", "IR팀 김민수", "해외 법인 보고 시간 반영", "Pending"),
    ("req-102", "전사 타운홀 장소를 대강당에서 온오프 병행으로 변경 요청", "인사팀 박서연", "지방 참석자 고려", "Pending"),
]

RSS_SOURCES = [
    ("company", "Sample Company Feed", "file:///C:/Users/LEE/Documents/codex/ceo-briefing-platform/samples/company-news.xml"),
    ("competitor", "Sample Competitor Feed", "file:///C:/Users/LEE/Documents/codex/ceo-briefing-platform/samples/competitor-news.xml"),
]

APP_SETTINGS = [
    ("app_name", "CEO Briefing Admin"),
    ("app_intro", "Admin console for calendar operations, RSS collection, and AI summary settings."),
    ("app_mode", "pilot"),
    ("preview_role", "ceo"),
    ("openai_api_key", ""),
    ("openai_model", "gpt-5.4-mini"),
    ("classification_model", "gpt-5.4-mini"),
    ("rss_keywords_company", ""),
    ("rss_keywords_competitor", ""),
    ("rss_filter_mode_company", "all"),
    ("rss_filter_mode_competitor", "all"),
    ("rss_auto_sync_last_run", ""),
]

CALENDAR_INTEGRATIONS = [
    ("page1", "google", "ceo@company.com", "CEO 일정 캘린더", "ceo@company.com", 0, "not_connected", ""),
    ("page2", "google", "company-events@company.com", "회사 주요 일정", "ops@company.com", 0, "not_connected", ""),
]


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO pages(id, title, description, page_type) VALUES (?, ?, ?, ?)", PAGES)
        conn.executemany("INSERT INTO page_permissions(page_id, role) VALUES (?, ?)", PERMISSIONS)
        conn.executemany(
            "INSERT INTO calendar_events(id, page_id, event_date, event_time, title, place, owner, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            CALENDAR_EVENTS,
        )
        conn.executemany("INSERT INTO update_requests(id, title, requester, reason, status) VALUES (?, ?, ?, ?, ?)", UPDATE_REQUESTS)
        conn.executemany("INSERT INTO rss_sources(feed_type, name, url) VALUES (?, ?, ?)", RSS_SOURCES)
        conn.executemany("INSERT INTO app_settings(key, value) VALUES (?, ?)", APP_SETTINGS)
        conn.executemany(
            """
            INSERT INTO calendar_integrations(
                page_id, provider, calendar_id, calendar_name, account_email, sync_enabled, status, last_synced_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            CALENDAR_INTEGRATIONS,
        )
        conn.commit()
        print(f"DB initialized: {DB_PATH}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
