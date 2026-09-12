from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "ceo_briefing.db"


def get_seoul_timezone():
    try:
        return ZoneInfo("Asia/Seoul")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=9))

DEFAULT_APP_SETTINGS = {
    "app_name": "CEO Briefing Admin",
    "app_intro": "Admin console for calendar operations, RSS collection, and AI summary settings.",
    "app_mode": "pilot",
    "preview_role": "ceo",
    "ai_provider": "openai",
    "openai_api_key": "",
    "gemini_api_key": "",
    "openai_model": "gpt-5.4-mini",
    "classification_model": "gpt-5.4-mini",
    "naver_client_id": "",
    "naver_client_secret": "",
    "rss_keywords_company": "한국항공우주산업, 한국항공우주, KAI, 강구영, 수리온, KF-21, FA-50, LAH",
    "rss_keywords_competitor": "한화에어로스페이스, 한화시스템, 한화오션, LIG넥스원, LIG Nex1, LIG D&A, 전투기, 자주포, 유도무기, 잠수함, 방산, 방위산업, 국방, 안보, 우주, 위성, 발사체, 우주항공청, KASA, 협력사",
    "rss_exclude_keywords_company": "rumor",
    "rss_exclude_keywords_competitor": "",
    "rss_filter_mode_company": "keywords",
    "rss_filter_mode_competitor": "keywords",
    "rss_auto_sync_last_run": "",
    "rss_last_checked_company": "",
    "rss_last_checked_competitor": "",
}

DEFAULT_CALENDAR_SETTINGS = {
    "page1": {
        "provider": "google",
        "calendar_id": "ceo@company.com",
        "calendar_name": "CEO Calendar",
        "account_email": "ceo@company.com",
        "sync_enabled": 0,
        "status": "not_connected",
        "last_synced_at": "",
    },
    "page2": {
        "provider": "google",
        "calendar_id": "company-events@company.com",
        "calendar_name": "Company Calendar",
        "account_email": "ops@company.com",
        "sync_enabled": 0,
        "status": "not_connected",
        "last_synced_at": "",
    },
}

DEFAULT_APP_USERS = [
    {"username": "admin", "pin": "4000", "role": "admin", "display_name": "Admin Console"},
    {"username": "ceo", "pin": "2000", "role": "ceo", "display_name": "CEO User"},
    {"username": "staff", "pin": "3000", "role": "staff", "display_name": "Staff User"},
]

ROLE_PAGE_ACCESS = {
    "admin": ["page1", "page2", "page3", "page5", "page6", "page7", "page8", "page9"],
    "ceo": ["page1", "page2", "page3"],
    "staff": ["page2", "page3"],
}


def db_exists() -> bool:
    return DB_PATH.exists()


_schema_initialized = False


def connect() -> sqlite3.Connection:
    global _schema_initialized
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.isolation_level = None
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    if not _schema_initialized:
        ensure_runtime_schema(conn)
        _schema_initialized = True
    return conn


def ensure_runtime_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS calendar_integrations (
            page_id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            calendar_id TEXT NOT NULL,
            calendar_name TEXT NOT NULL,
            account_email TEXT NOT NULL,
            sync_enabled INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'not_connected',
            last_synced_at TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS calendar_events (
            id TEXT PRIMARY KEY,
            page_id TEXT NOT NULL,
            event_date TEXT NOT NULL,
            event_time TEXT NOT NULL,
            title TEXT NOT NULL,
            place TEXT NOT NULL,
            owner TEXT NOT NULL,
            status TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS app_users (
            username TEXT PRIMARY KEY,
            pin TEXT NOT NULL,
            role TEXT NOT NULL,
            display_name TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS deleted_feed_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            feed_type TEXT NOT NULL,
            link TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            deleted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(feed_type, link)
        );

        CREATE TABLE IF NOT EXISTS disclosure_logs (
            rcept_no TEXT PRIMARY KEY,
            corp_code TEXT NOT NULL,
            corp_name TEXT NOT NULL,
            report_nm TEXT NOT NULL,
            rcept_dt TEXT NOT NULL,
            sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    columns = [row[1] for row in conn.execute("PRAGMA table_info(calendar_events)").fetchall()]
    if "event_date" not in columns:
        conn.execute("ALTER TABLE calendar_events ADD COLUMN event_date TEXT NOT NULL DEFAULT '2026-04-07'")
    rss_columns = [row[1] for row in conn.execute("PRAGMA table_info(rss_sources)").fetchall()]
    if "include_keywords" not in rss_columns:
        conn.execute("ALTER TABLE rss_sources ADD COLUMN include_keywords TEXT NOT NULL DEFAULT ''")
    if "exclude_keywords" not in rss_columns:
        conn.execute("ALTER TABLE rss_sources ADD COLUMN exclude_keywords TEXT NOT NULL DEFAULT ''")
    feed_columns = [row[1] for row in conn.execute("PRAGMA table_info(feed_items)").fetchall()]
    if "article_published_at" not in feed_columns:
        conn.execute("ALTER TABLE feed_items ADD COLUMN article_published_at TEXT")
    if "article_publisher" not in feed_columns:
        conn.execute("ALTER TABLE feed_items ADD COLUMN article_publisher TEXT NOT NULL DEFAULT ''")
    if "article_category" not in feed_columns:
        conn.execute("ALTER TABLE feed_items ADD COLUMN article_category TEXT NOT NULL DEFAULT 'reference'")
    if "category_manual" not in feed_columns:
        conn.execute("ALTER TABLE feed_items ADD COLUMN category_manual INTEGER NOT NULL DEFAULT 0")
    if "sent_briefing_at" not in feed_columns:
        conn.execute("ALTER TABLE feed_items ADD COLUMN sent_briefing_at TEXT DEFAULT NULL")

    for key, value in DEFAULT_APP_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO app_settings(key, value) VALUES (?, ?)", (key, str(value)))

    for page_id, payload in DEFAULT_CALENDAR_SETTINGS.items():
        conn.execute(
            """
            INSERT OR IGNORE INTO calendar_integrations(
                page_id, provider, calendar_id, calendar_name, account_email, sync_enabled, status, last_synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                page_id,
                payload["provider"],
                payload["calendar_id"],
                payload["calendar_name"],
                payload["account_email"],
                payload["sync_enabled"],
                payload["status"],
                payload["last_synced_at"],
            ),
        )

    conn.execute("UPDATE pages SET title = ?, description = ?, page_type = ? WHERE id = ?", ("CEO 일정", "CEO calendar and critical meetings.", "calendar", "page1"))
    conn.execute("UPDATE pages SET title = ?, description = ?, page_type = ? WHERE id = ?", ("회사 주요 일정", "Company calendar and approval workflow.", "calendar", "page2"))
    conn.execute("UPDATE pages SET title = ?, description = ?, page_type = ? WHERE id = ?", ("주요 기사", "Manage published and new article queue.", "feed", "page3"))
    conn.execute("UPDATE pages SET title = ?, description = ?, page_type = ? WHERE id = ?", ("경쟁사 동향", "Manage RSS sources and article queue for competitors.", "feed", "page4"))
    conn.execute("UPDATE pages SET title = ?, description = ?, page_type = ? WHERE id = ?", ("캘린더 설정", "Configure Google Calendar connections and write access.", "calendar_settings", "page5"))
    conn.execute(
        "INSERT OR IGNORE INTO pages(id, title, description, page_type) VALUES (?, ?, ?, ?)",
        ("page6", "AI 설정", "Store AI provider, API key, and models for RSS classification and summarization.", "openai_settings"),
    )
    conn.execute(
        "UPDATE pages SET title = ?, description = ? WHERE id = ?",
        ("AI 설정", "Store AI provider, API key, and models for RSS classification and summarization.", "page6"),
    )

    conn.execute(
        "INSERT OR IGNORE INTO pages(id, title, description, page_type) VALUES (?, ?, ?, ?)",
        ("page7", "APP 사용자", "Manage APP users and role access.", "app_users"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO pages(id, title, description, page_type) VALUES (?, ?, ?, ?)",
        ("page8", "언론사 추가", "Manage RSS sources and Naver News search settings.", "rss_settings"),
    )
    conn.execute("UPDATE pages SET title = ?, description = ?, page_type = ? WHERE id = ?", ("언론사 추가", "Manage RSS sources and Naver News search settings.", "rss_settings", "page8"))

    conn.execute(
        "INSERT OR IGNORE INTO pages(id, title, description, page_type) VALUES (?, ?, ?, ?)",
        ("page9", "텔레그램 전송", "Send published articles briefing to Telegram.", "telegram_send"),
    )
    conn.execute("UPDATE pages SET title = ?, description = ?, page_type = ? WHERE id = ?", ("텔레그램 전송", "Send published articles briefing to Telegram.", "telegram_send", "page9"))

    for user in DEFAULT_APP_USERS:
        conn.execute(
            """
            INSERT OR IGNORE INTO app_users(username, pin, role, display_name, active)
            VALUES (?, ?, ?, ?, 1)
            """,
            (user["username"], user["pin"], user["role"], user["display_name"]),
        )

    conn.execute("DELETE FROM page_permissions")
    conn.executemany(
        "INSERT OR IGNORE INTO page_permissions(page_id, role) VALUES (?, ?)",
        [(page_id, role) for role, page_ids in ROLE_PAGE_ACCESS.items() for page_id in page_ids],
    )
    conn.commit()


def get_pages_for_role(role: str) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT p.id, p.title, p.description, p.page_type
            FROM pages p
            JOIN page_permissions pp ON pp.page_id = p.id
            WHERE pp.role = ?
            ORDER BY p.id
            """,
            (role,),
        ).fetchall()
    return [dict(row) for row in rows]


def role_has_page_access(role: str, page_id: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM page_permissions WHERE role = ? AND page_id = ?",
            (role, page_id),
        ).fetchone()
    return row is not None


def authenticate_app_user(username: str, pin: str) -> Dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT username, role, display_name
            FROM app_users
            WHERE username = ? AND pin = ? AND active = 1
            """,
            (username, pin),
        ).fetchone()
    return dict(row) if row else None


def list_app_users() -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT username, role, display_name, active
            FROM app_users
            ORDER BY
              CASE role
                WHEN 'admin' THEN 0
                WHEN 'ceo' THEN 1
                ELSE 2
              END,
              username
            """
        ).fetchall()
    return [dict(row) for row in rows]


def create_app_user(username: str, pin: str, role: str, display_name: str) -> Dict[str, Any]:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO app_users(username, pin, role, display_name, active)
            VALUES (?, ?, ?, ?, 1)
            """,
            (username, pin, role, display_name),
        )
        conn.commit()
        row = conn.execute(
            "SELECT username, role, display_name, active FROM app_users WHERE username = ?",
            (username,),
        ).fetchone()
    return dict(row)


def update_app_user(username: str, payload: Dict[str, Any]) -> Dict[str, Any] | None:
    with connect() as conn:
        exists = conn.execute("SELECT username FROM app_users WHERE username = ?", (username,)).fetchone()
        if not exists:
            return None
        conn.execute(
            """
            UPDATE app_users
            SET pin = COALESCE(?, pin),
                role = ?,
                display_name = ?,
                active = ?
            WHERE username = ?
            """,
            (
                payload.get("pin") or None,
                payload["role"],
                payload["display_name"],
                int(payload["active"]),
                username,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT username, role, display_name, active FROM app_users WHERE username = ?",
            (username,),
        ).fetchone()
    return dict(row) if row else None


def delete_app_user(username: str) -> bool:
    with connect() as conn:
        conn.execute("DELETE FROM app_users WHERE username = ? AND role != 'admin'", (username,))
        deleted = conn.total_changes > 0
        conn.commit()
    return deleted


def get_calendar(page_id: str) -> Dict[str, Any]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, event_date AS date, event_time AS time, title, place, owner, status
            FROM calendar_events
            WHERE page_id = ?
            ORDER BY event_date, event_time
            """,
            (page_id,),
        ).fetchall()
    return {"events": [dict(row) for row in rows]}


def create_calendar_event(page_id: str, event_date: str, event_time: str, title: str, place: str, owner: str, status: str) -> Dict[str, Any]:
    with connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM calendar_events WHERE page_id = ?", (page_id,)).fetchone()[0]
        next_seq = 100 + count + 1
        while conn.execute("SELECT 1 FROM calendar_events WHERE id = ?", (f"{page_id}-evt-{next_seq}",)).fetchone():
            next_seq += 1
        next_id = f"{page_id}-evt-{next_seq}"
        conn.execute(
            """
            INSERT INTO calendar_events(id, page_id, event_date, event_time, title, place, owner, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (next_id, page_id, event_date, event_time, title, place, owner, status),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT id, event_date AS date, event_time AS time, title, place, owner, status
            FROM calendar_events
            WHERE id = ?
            """,
            (next_id,),
        ).fetchone()
    return dict(row)


def update_calendar_event(event_id: str, page_id: str, event_date: str, event_time: str, title: str, place: str, owner: str, status: str) -> Dict[str, Any] | None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE calendar_events
            SET event_date = ?, event_time = ?, title = ?, place = ?, owner = ?, status = ?
            WHERE id = ? AND page_id = ?
            """,
            (event_date, event_time, title, place, owner, status, event_id, page_id),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT id, event_date AS date, event_time AS time, title, place, owner, status
            FROM calendar_events
            WHERE id = ? AND page_id = ?
            """,
            (event_id, page_id),
        ).fetchone()
    return dict(row) if row else None


def delete_calendar_event(event_id: str, page_id: str) -> bool:
    with connect() as conn:
        conn.execute("DELETE FROM calendar_events WHERE id = ? AND page_id = ?", (event_id, page_id))
        deleted = conn.total_changes > 0
        conn.commit()
    return deleted


def list_requests() -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, title, requester, reason, status, created_at
            FROM update_requests
            ORDER BY created_at DESC, id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def create_request(title: str, requester: str, reason: str) -> Dict[str, Any]:
    with connect() as conn:
        next_id = f"req-{100 + conn.execute('SELECT COUNT(*) FROM update_requests').fetchone()[0] + 1}"
        conn.execute(
            """
            INSERT INTO update_requests(id, title, requester, reason, status)
            VALUES (?, ?, ?, ?, 'Pending')
            """,
            (next_id, title, requester, reason),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, title, requester, reason, status, created_at FROM update_requests WHERE id = ?",
            (next_id,),
        ).fetchone()
    return dict(row)


def update_request_status(request_id: str, status: str) -> Dict[str, Any] | None:
    with connect() as conn:
        conn.execute("UPDATE update_requests SET status = ? WHERE id = ?", (status, request_id))
        conn.commit()
        row = conn.execute(
            "SELECT id, title, requester, reason, status, created_at FROM update_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
    return dict(row) if row else None


def get_feed(feed_type: str) -> Dict[str, List[Dict[str, Any]]]:
    with connect() as conn:
        published = conn.execute(
            """
            SELECT id, title, summary, link, source, article_published_at, article_publisher, article_category, selected
            FROM feed_items
            WHERE feed_type = ? AND published = 1
            ORDER BY COALESCE(article_published_at, published_at) DESC, id DESC
            """,
            (feed_type,),
        ).fetchall()
        queued = conn.execute(
            """
            SELECT id, title, summary, link, source, article_published_at, article_publisher, article_category, selected
            FROM feed_items
            WHERE feed_type = ? AND published = 0
            ORDER BY COALESCE(article_published_at, published_at) DESC, id DESC
            """,
            (feed_type,),
        ).fetchall()
    return {"published": [dict(row) for row in published], "queued": [dict(row) for row in queued]}


def toggle_feed_item(item_id: str) -> Dict[str, Any] | None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE feed_items
            SET selected = CASE selected WHEN 1 THEN 0 ELSE 1 END
            WHERE id = ? AND published = 0
            """,
            (item_id,),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, title, summary, link, source, article_published_at, article_publisher, article_category, selected FROM feed_items WHERE id = ?",
            (item_id,),
        ).fetchone()
    return dict(row) if row else None


def publish_feed_item(item_id: str, category: str | None = None) -> Dict[str, Any] | None:
    allowed = {"kai", "government", "competitor", "hanwha", "lig", "space", "reference"}
    target = (category or "").strip().lower()
    if target and target not in allowed:
        return None
    with connect() as conn:
        if target:
            conn.execute(
                """
                UPDATE feed_items
                SET article_category = ?, category_manual = 1, published = 1, published_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (target, item_id),
            )
        else:
            conn.execute(
                """
                UPDATE feed_items
                SET published = 1, published_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (item_id,),
            )
        conn.commit()
        row = conn.execute(
            "SELECT id, title, summary, link, source, article_published_at, article_publisher, article_category, selected FROM feed_items WHERE id = ?",
            (item_id,),
        ).fetchone()
    return dict(row) if row else None


def update_feed_item_category(item_id: str, category: str) -> Dict[str, Any] | None:
    allowed = {"kai", "government", "competitor", "hanwha", "lig", "space", "reference"}
    target = category.strip().lower()
    if target not in allowed:
        return None
    with connect() as conn:
        conn.execute(
            """
            UPDATE feed_items
            SET article_category = ?, category_manual = 1
            WHERE id = ?
            """,
            (target, item_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, title, summary, link, source, article_published_at, article_publisher, article_category, selected FROM feed_items WHERE id = ?",
            (item_id,),
        ).fetchone()
    return dict(row) if row else None


def delete_feed_item(item_id: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT feed_type, link, title FROM feed_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            return False
        conn.execute(
            "DELETE FROM feed_items WHERE id = ?",
            (item_id,),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO deleted_feed_items(feed_type, link, title)
            VALUES (?, ?, ?)
            """,
            (row["feed_type"], row["link"], row["title"]),
        )
    return True


def list_rss_sources() -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, feed_type, name, url, active, include_keywords, exclude_keywords
            FROM rss_sources
            ORDER BY feed_type, id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def list_rss_sources_by_type(feed_type: str) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, feed_type, name, url, active, include_keywords, exclude_keywords
            FROM rss_sources
            WHERE feed_type = ?
            ORDER BY id DESC
            """,
            (feed_type,),
        ).fetchall()
    return [dict(row) for row in rows]


def create_rss_source(feed_type: str, name: str, url: str) -> Dict[str, Any]:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO rss_sources(feed_type, name, url, active, include_keywords, exclude_keywords)
            VALUES (?, ?, ?, 1, '', '')
            """,
            (feed_type, name, url),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT id, feed_type, name, url, active, include_keywords, exclude_keywords
            FROM rss_sources
            WHERE url = ?
            """,
            (url,),
        ).fetchone()
    return dict(row)


def delete_rss_source(source_id: int) -> bool:
    with connect() as conn:
        conn.execute("DELETE FROM rss_sources WHERE id = ?", (source_id,))
        deleted = conn.total_changes > 0
        conn.commit()
    return deleted


def update_rss_source_keywords(source_id: int, include_keywords: str, exclude_keywords: str) -> Dict[str, Any] | None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE rss_sources
            SET include_keywords = ?, exclude_keywords = ?
            WHERE id = ?
            """,
            (include_keywords, exclude_keywords, source_id),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT id, feed_type, name, url, active, include_keywords, exclude_keywords
            FROM rss_sources
            WHERE id = ?
            """,
            (source_id,),
        ).fetchone()
    return dict(row) if row else None


def get_app_settings() -> Dict[str, Any]:
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM app_settings ORDER BY key").fetchall()
    settings = {row["key"]: row["value"] for row in rows}
    settings["preview_role"] = settings.get("preview_role", "ceo")
    return settings


def update_app_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    with connect() as conn:
        for key, value in payload.items():
            conn.execute(
                """
                INSERT INTO app_settings(key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, str(value)),
            )
        conn.commit()
    return get_app_settings()


def get_openai_settings() -> Dict[str, Any]:
    settings = get_app_settings()
    provider = settings.get("ai_provider", "openai") or "openai"
    provider = provider if provider in {"openai", "gemini"} else "openai"
    openai_api_key = settings.get("openai_api_key", "")
    gemini_api_key = settings.get("gemini_api_key", "")
    api_key = gemini_api_key if provider == "gemini" else openai_api_key

    def mask_key(value: str) -> str:
        if not value:
            return ""
        return f"{value[:7]}...{value[-4:]}" if len(value) > 11 else "saved"

    default_model = "gemini-3.7-flash" if provider == "gemini" else "gpt-5.4-mini"
    return {
        "provider": provider,
        "has_api_key": bool(api_key),
        "api_key_masked": mask_key(api_key),
        "has_openai_api_key": bool(openai_api_key),
        "openai_api_key_masked": mask_key(openai_api_key),
        "has_gemini_api_key": bool(gemini_api_key),
        "gemini_api_key_masked": mask_key(gemini_api_key),
        "model": settings.get("openai_model", default_model),
        "classification_model": settings.get("classification_model", default_model),
    }


def update_openai_settings(api_key: str | None, model: str, classification_model: str | None = None, provider: str = "openai") -> Dict[str, Any]:
    provider = provider if provider in {"openai", "gemini"} else "openai"
    payload: Dict[str, Any] = {"ai_provider": provider, "openai_model": model}
    if classification_model:
        payload["classification_model"] = classification_model
    if api_key:
        payload["gemini_api_key" if provider == "gemini" else "openai_api_key"] = api_key
    update_app_settings(payload)
    return get_openai_settings()


def get_naver_settings() -> Dict[str, Any]:
    settings = get_app_settings()
    client_id = settings.get("naver_client_id", "")
    client_secret = settings.get("naver_client_secret", "")
    client_id_masked = ""
    client_secret_masked = ""
    if client_id:
        client_id_masked = f"{client_id[:4]}...{client_id[-2:]}" if len(client_id) > 6 else "saved"
    if client_secret:
        client_secret_masked = f"{client_secret[:4]}...{client_secret[-2:]}" if len(client_secret) > 6 else "saved"
    return {
        "has_client_id": bool(client_id),
        "has_client_secret": bool(client_secret),
        "client_id_masked": client_id_masked,
        "client_secret_masked": client_secret_masked,
    }


def update_naver_settings(client_id: str | None, client_secret: str | None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if client_id:
        payload["naver_client_id"] = client_id
    if client_secret:
        payload["naver_client_secret"] = client_secret
    if payload:
        update_app_settings(payload)
    return get_naver_settings()


def get_rss_keywords(feed_type: str) -> Dict[str, Any]:
    settings = get_app_settings()
    raw = settings.get(f"rss_keywords_{feed_type}", "")
    exclude_raw = settings.get(f"rss_exclude_keywords_{feed_type}", "")
    mode = settings.get(f"rss_filter_mode_{feed_type}", "all")
    keywords = [item.strip() for item in raw.split(",") if item.strip()]
    exclude_keywords = [item.strip() for item in exclude_raw.split(",") if item.strip()]
    return {
        "feed_type": feed_type,
        "mode": mode,
        "raw": raw,
        "keywords": keywords,
        "exclude_raw": exclude_raw,
        "exclude_keywords": exclude_keywords,
    }


def update_rss_keywords(feed_type: str, raw_keywords: str, exclude_raw_keywords: str, mode: str) -> Dict[str, Any]:
    update_app_settings(
        {
            f"rss_keywords_{feed_type}": raw_keywords,
            f"rss_exclude_keywords_{feed_type}": exclude_raw_keywords,
            f"rss_filter_mode_{feed_type}": mode,
        }
    )
    return get_rss_keywords(feed_type)


def get_sync_status() -> Dict[str, Any]:
    settings = get_app_settings()
    seoul = get_seoul_timezone()
    now = datetime.now(seoul).isoformat(timespec="seconds")
    return {
        "last_run": settings.get("rss_auto_sync_last_run", ""),
        "window": "Every 30 minutes",
        "company_last_checked": settings.get("rss_last_checked_company", ""),
        "competitor_last_checked": settings.get("rss_last_checked_competitor", ""),
        "now": now,
    }


def list_calendar_integrations() -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT page_id, provider, calendar_id, calendar_name, account_email, sync_enabled, status, last_synced_at
            FROM calendar_integrations
            ORDER BY page_id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def update_calendar_integration(page_id: str, payload: Dict[str, Any]) -> Dict[str, Any] | None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE calendar_integrations
            SET provider = ?, calendar_id = ?, calendar_name = ?, account_email = ?, sync_enabled = ?, status = ?, last_synced_at = ?
            WHERE page_id = ?
            """,
            (
                payload["provider"],
                payload["calendar_id"],
                payload["calendar_name"],
                payload["account_email"],
                int(payload["sync_enabled"]),
                payload["status"],
                payload["last_synced_at"],
                page_id,
            ),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT page_id, provider, calendar_id, calendar_name, account_email, sync_enabled, status, last_synced_at
            FROM calendar_integrations
            WHERE page_id = ?
            """,
            (page_id,),
        ).fetchone()
    return dict(row) if row else None


def get_admin_snapshot() -> Dict[str, Any]:
    return {
        "app": get_app_settings(),
        "calendar_integrations": list_calendar_integrations(),
        "rss_sources": list_rss_sources(),
        "openai": get_openai_settings(),
        "naver": get_naver_settings(),
        "rss_keywords": {
            "company": get_rss_keywords("company"),
            "competitor": get_rss_keywords("competitor"),
        },
        "sync_status": get_sync_status(),
    }
