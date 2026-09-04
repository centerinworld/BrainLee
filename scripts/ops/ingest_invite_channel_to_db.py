#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from telethon import TelegramClient
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest

DB_PATH = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"
SESSION = os.getenv("TELEGRAM_SESSION_PATH", "/Volumes/Realtek_NVME/stock_dashboard/runtime/telegram_session_scan")
REPORTS_DIR = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime/reports")


def _load_env(path: str = "/Volumes/Realtek_NVME/stock_dashboard/runtime/.env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=120000")
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS telegram_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel TEXT,
            message_id INTEGER,
            text TEXT,
            date TEXT,
            summary TEXT,
            stocks TEXT,
            collected_at TEXT,
            UNIQUE(channel, message_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS report_files (
            id INTEGER PRIMARY KEY,
            channel_id TEXT,
            message_id INTEGER,
            stock_code TEXT,
            stock_name TEXT,
            report_date TEXT,
            file_name TEXT,
            saved_name TEXT,
            file_path TEXT,
            file_size INTEGER,
            mime_type TEXT,
            caption TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            sector TEXT DEFAULT '',
            posted_date TEXT DEFAULT '',
            UNIQUE(channel_id, message_id, file_name)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tm_ch_mid ON telegram_messages(channel, message_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rf_ch_mid ON report_files(channel_id, message_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rf_stock ON report_files(stock_code)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS detailed_analysis_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT,
            stock_name TEXT NOT NULL,
            title TEXT NOT NULL,
            content_md TEXT NOT NULL,
            source TEXT DEFAULT 'manual',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS detailed_analysis_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            file_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_type TEXT DEFAULT 'xlsx',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(post_id) REFERENCES detailed_analysis_posts(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dap_stock ON detailed_analysis_posts(stock_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_daf_post ON detailed_analysis_files(post_id)")


def _extract_report_date(text: str, dt: datetime) -> str:
    t = text or ""
    m = re.search(r"(20\d{2})[.\-/년 ]\s*(\d{1,2})", t)
    if m:
        y = int(m.group(1))
        mo = int(m.group(2))
        if 1 <= mo <= 12:
            return f"{y:04d}-{mo:02d}-01"
    return dt.strftime("%Y-%m-%d")


def _normalize_name(name: str) -> str:
    return re.sub(r"\s+", "", (name or "").strip())


def _pick_stock_matches(text: str, stock_dict: dict[str, tuple[str, str]]) -> list[tuple[str, str]]:
    if not text:
        return []
    norm_text = _normalize_name(text)
    found: list[tuple[str, str]] = []
    for nm, (code, raw_name) in stock_dict.items():
        if nm and nm in norm_text:
            found.append((code, raw_name))
    # 중복 제거
    uniq = {}
    for c, n in found:
        if c not in uniq:
            uniq[c] = n
    return [(c, uniq[c]) for c in uniq]


async def ingest(invite_link: str, limit: int = 0, offset_id: int = 0) -> dict[str, Any]:
    _load_env()
    global SESSION
    SESSION = os.getenv("TELEGRAM_SESSION_PATH", SESSION)
    api_id = int(os.getenv("TELEGRAM_API_ID", "0") or "0")
    api_hash = os.getenv("TELEGRAM_API_HASH", "")
    if not api_id or not api_hash:
        raise RuntimeError("TELEGRAM_API_ID / TELEGRAM_API_HASH 설정이 필요합니다.")

    invite_hash = invite_link.split("+", 1)[1].strip()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    with _conn() as conn:
        _ensure_tables(conn)
        universe = conn.execute(
            """
            SELECT DISTINCT stock_code, stock_name
            FROM stock_universe
            WHERE stock_code IS NOT NULL AND stock_name IS NOT NULL
            """
        ).fetchall()
        stock_dict = {_normalize_name(r["stock_name"]): (r["stock_code"], r["stock_name"]) for r in universe}

    client = TelegramClient(SESSION, api_id, api_hash)
    await client.connect()
    channel = None
    channel_title = "invite_channel"
    try:
        imp = await client(ImportChatInviteRequest(invite_hash))
        channel = getattr(imp, "chats", [None])[0]
        if channel is not None:
            channel_title = getattr(channel, "title", channel_title)
    except Exception:
        chk = await client(CheckChatInviteRequest(invite_hash))
        channel = getattr(chk, "chat", None)
        if channel is None:
            channel = await client.get_entity(invite_link)
        channel_title = getattr(channel, "title", channel_title)

    inserted_msgs = 0
    inserted_files = 0
    created_posts = 0
    attached_files = 0

    with _conn() as conn:
        _ensure_tables(conn)

        count = 0
        last_message_id = offset_id
        async for m in client.iter_messages(
            channel,
            reverse=True,
            limit=limit if limit > 0 else None,
            min_id=offset_id if offset_id > 0 else 0,
        ):
            count += 1
            if int(m.id) > last_message_id:
                last_message_id = int(m.id)
            txt = (m.message or "").strip()
            dt = m.date
            date_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            report_date = _extract_report_date(txt, dt)

            matches = _pick_stock_matches(txt, stock_dict)
            stocks_json = json.dumps([name for _, name in matches], ensure_ascii=False)

            conn.execute(
                """
                INSERT OR REPLACE INTO telegram_messages
                (channel, message_id, text, date, summary, stocks, collected_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (channel_title, int(m.id), txt, date_str, "", stocks_json),
            )
            inserted_msgs += 1

            if m.file:
                try:
                    fname = getattr(m.file, "name", "") or f"{m.id}_{int(dt.timestamp())}"
                    safe_name = fname.replace("/", "_").replace("\\", "_")
                    save_path = REPORTS_DIR / f"{dt.strftime('%Y%m%d')}_{safe_name}"
                    await client.download_media(m, file=str(save_path))
                    size = save_path.stat().st_size if save_path.exists() else None
                    mime = getattr(m.file, "mime_type", "") or ""

                    # 메시지에 종목명이 여러 개면 우선 첫 종목에 연결 + 파일명에서 추가 추정
                    code = matches[0][0] if matches else ""
                    name = matches[0][1] if matches else ""
                    if not name:
                        fn_matches = _pick_stock_matches(safe_name, stock_dict)
                        if fn_matches:
                            code, name = fn_matches[0]

                    conn.execute(
                        """
                        INSERT OR IGNORE INTO report_files
                        (channel_id, message_id, stock_code, stock_name, report_date,
                         file_name, saved_name, file_path, file_size, mime_type, caption, posted_date)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            channel_title,
                            int(m.id),
                            code,
                            name,
                            report_date,
                            safe_name,
                            save_path.name,
                            str(save_path),
                            size,
                            mime,
                            txt,
                            dt.strftime("%Y-%m-%d"),
                        ),
                    )
                    if conn.total_changes > 0:
                        inserted_files += 1

                    # 엑셀 기반 기업글 자동 생성(없으면)
                    ext = save_path.suffix.lower()
                    if ext in {".xlsx", ".xls"} and name:
                        ex = conn.execute("SELECT id FROM detailed_analysis_posts WHERE stock_name=? LIMIT 1", (name,)).fetchone()
                        if ex:
                            post_id = ex["id"]
                        else:
                            cur = conn.execute(
                                """
                                INSERT INTO detailed_analysis_posts (stock_code, stock_name, title, content_md, source)
                                VALUES (?, ?, ?, ?, 'telegram_invite_excel_auto')
                                """,
                                (
                                    code,
                                    name,
                                    f"{name} 상세분석",
                                    f"# {name} 투자관점 상세분석\n\n- 텔레그램 첨부 엑셀 기반 자동 생성 초안입니다.\n- 이후 GPT mini 맥락 요약이 누적됩니다.",
                                ),
                            )
                            post_id = cur.lastrowid
                            created_posts += 1

                        fex = conn.execute(
                            """
                            SELECT 1 FROM detailed_analysis_files
                            WHERE post_id=? AND file_path=?
                            LIMIT 1
                            """,
                            (post_id, str(save_path)),
                        ).fetchone()
                        if not fex:
                            conn.execute(
                                """
                                INSERT INTO detailed_analysis_files (post_id, file_name, file_path, file_type)
                                VALUES (?, ?, ?, ?)
                                """,
                                (post_id, save_path.name, str(save_path), "telegram"),
                            )
                            attached_files += 1
                except Exception:
                    pass

        conn.commit()

    await client.disconnect()
    return {
        "channel_title": channel_title,
        "processed_messages": count,
        "last_message_id": last_message_id,
        "upserted_messages": inserted_msgs,
        "inserted_files": inserted_files,
        "created_posts_from_excel": created_posts,
        "attached_files_to_posts": attached_files,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--invite", required=True, help="예: https://t.me/+xxxxxxxx")
    ap.add_argument("--limit", type=int, default=0, help="0이면 전체")
    ap.add_argument("--offset-id", type=int, default=0, help="해당 메시지 ID 초과분부터 수집")
    args = ap.parse_args()
    out = asyncio.run(ingest(args.invite.strip(), args.limit, args.offset_id))
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
