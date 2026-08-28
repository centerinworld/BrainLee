from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT_DIR.parent
DB_PATH = ROOT_DIR / "data" / "hs_trade_lab.db"
SESSION_PATH = PROJECT_ROOT / "telegram_session"
CHANNEL = "BeOn_BeClear"

sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(ROOT_DIR / "scripts"))

from backfill_telegram_posts import (  # noqa: E402
    TelegramPost,
    create_table,
    extract_companies,
    extract_title,
    infer_sector_key,
    upsert_posts,
)


def get_config() -> dict[str, str]:
    try:
        import config as cfg

        return {
            "api_id": str(getattr(cfg, "TELEGRAM_API_ID", os.getenv("TELEGRAM_API_ID", ""))),
            "api_hash": str(getattr(cfg, "TELEGRAM_API_HASH", os.getenv("TELEGRAM_API_HASH", ""))),
            "phone": str(getattr(cfg, "TELEGRAM_PHONE", os.getenv("TELEGRAM_PHONE", ""))),
        }
    except Exception:
        return {
            "api_id": os.getenv("TELEGRAM_API_ID", ""),
            "api_hash": os.getenv("TELEGRAM_API_HASH", ""),
            "phone": os.getenv("TELEGRAM_PHONE", ""),
        }


def message_url(message_id: int) -> str:
    return f"https://t.me/{CHANNEL}/{message_id}"


async def fetch_posts(*, limit: int, lookback_days: int) -> list[TelegramPost]:
    from telethon import TelegramClient

    cfg = get_config()
    if not cfg["api_id"] or not cfg["api_hash"]:
        raise RuntimeError("TELEGRAM_API_ID / TELEGRAM_API_HASH 설정이 필요합니다.")
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    client = TelegramClient(str(SESSION_PATH), int(cfg["api_id"]), cfg["api_hash"])
    await client.start(phone=cfg["phone"] or None)
    posts: list[TelegramPost] = []
    try:
        entity = await client.get_entity(CHANNEL)
        async for message in client.iter_messages(entity, limit=limit):
            if not getattr(message, "message", None):
                continue
            posted_dt = message.date
            if posted_dt and posted_dt.tzinfo is None:
                posted_dt = posted_dt.replace(tzinfo=timezone.utc)
            if posted_dt and posted_dt < cutoff:
                break
            text = str(message.message or "").strip()
            if not text:
                continue
            title = extract_title(text)
            companies = extract_companies(text)
            company_count = len(companies)
            mapping_scope = "company" if 0 < company_count <= 2 else "sector"
            posts.append(
                TelegramPost(
                    message_id=str(message.id),
                    posted_at=posted_dt.isoformat() if posted_dt else "",
                    url=message_url(message.id),
                    text=text,
                    title=title,
                    companies=companies,
                    company_count=company_count,
                    mapping_scope=mapping_scope,
                    sector_key=infer_sector_key(title, text),
                )
            )
    finally:
        await client.disconnect()
    return posts


async def run(limit: int, lookback_days: int) -> dict[str, object]:
    posts = await fetch_posts(limit=limit, lookback_days=lookback_days)
    conn = sqlite3.connect(DB_PATH)
    create_table(conn)
    summary = upsert_posts(conn, posts)
    conn.commit()
    bounds = conn.execute(
        "SELECT COUNT(*), MIN(substr(posted_at,1,10)), MAX(substr(posted_at,1,10)) FROM telegram_post_cache"
    ).fetchone()
    conn.close()
    return {
        "channel": f"@{CHANNEL}",
        "fetched_posts": len(posts),
        "summary": summary,
        "cache_total": bounds[0],
        "cache_min_date": bounds[1],
        "cache_max_date": bounds[2],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill @BeOn_BeClear through Telethon session.")
    parser.add_argument("--limit", type=int, default=20000)
    parser.add_argument("--lookback-days", type=int, default=370)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.limit, args.lookback_days)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
