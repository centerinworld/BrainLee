"""
telegram_store.py — 텔레그램 채널별 수집 옵션 + 채팅 본문 저장 스키마 (2026-10-04)

수집기(telegram_collector.py)와 관리자 API(routes/telegram.py)가 같이 쓴다.
telethon 등 무거운 의존성이 없어 어디서든 import 가능.

테이블
  telegram_channels        (기존) + collect_pdf / collect_text / collect_photo 옵션 컬럼
  telegram_channel_posts   (신규) 채널 메시지 본문 원문 저장 — LLM 없이 수집만
저장 경로
  PDF/문서  /Volumes/Realtek_NVME/stock_dashboard/reports            (기존 그대로)
  사진      /Volumes/Realtek_NVME/stock_dashboard/telegram_media/photos/<채널>/<message_id>.jpg
"""
import re
from pathlib import Path

ROOT = Path("/Volumes/Realtek_NVME/stock_dashboard")
MEDIA_DIR = ROOT / "telegram_media"
PHOTO_DIR = MEDIA_DIR / "photos"

# 기존 채널은 PDF 수집만 하던 상태 → PDF 유지 + 본문 켜기(요청 사항), 사진은 꺼둠
DEFAULT_FLAGS = {"collect_pdf": 1, "collect_text": 1, "collect_photo": 0}


def safe_dirname(channel_id: str) -> str:
    s = re.sub(r"[^0-9A-Za-z가-힣_.-]+", "_", str(channel_id)).strip("_")
    return s[:60] or "channel"


def ensure_schema(conn) -> None:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(telegram_channels)").fetchall()]
    for col, default in DEFAULT_FLAGS.items():
        if col not in cols:
            conn.execute(f"ALTER TABLE telegram_channels ADD COLUMN {col} INTEGER DEFAULT {default}")
    if "entity_hint" not in cols:
        conn.execute("ALTER TABLE telegram_channels ADD COLUMN entity_hint TEXT")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS telegram_channel_posts (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id    TEXT NOT NULL,
            message_id    INTEGER NOT NULL,
            msg_date      TEXT,
            text          TEXT,
            has_photo     INTEGER DEFAULT 0,
            photo_path    TEXT,
            has_document  INTEGER DEFAULT 0,
            file_name     TEXT,
            views         INTEGER,
            forwards      INTEGER,
            sender        TEXT,
            fwd_from      TEXT,
            reply_to_id   INTEGER,
            collected_at  TEXT,
            UNIQUE(channel_id, message_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tcp_channel_date ON telegram_channel_posts(channel_id, msg_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tcp_date ON telegram_channel_posts(msg_date)")
    conn.commit()


def flags_of(row: dict) -> dict:
    """채널 dict에서 수집 옵션을 bool로 읽는다(없으면 기본값)."""
    out = {}
    for k, d in DEFAULT_FLAGS.items():
        v = row.get(k)
        out[k] = bool(d if v is None else v)
    return out
