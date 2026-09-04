"""
report_store.py

보고서 저장소 분리용 유틸:
- 메인 DB(stock.db)와 분리된 보고서 전용 DB 사용
- report_files 조회/다운로드 시 공용으로 사용
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

STOCK_DB_PATH = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db")
REPORTS_DB_PATH = Path(os.getenv("REPORTS_DB_PATH", "/Volumes/Realtek_NVME/stock_dashboard/runtime/data/reports_catalog.db"))
REPORTS_DIR = Path(os.getenv("REPORTS_DIR", "/Volumes/Realtek_NVME/stock_dashboard/runtime/reports"))


def connect_reports_db() -> sqlite3.Connection:
    REPORTS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(REPORTS_DB_PATH), timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    ensure_reports_schema(conn)
    return conn


def connect_stock_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(STOCK_DB_PATH), timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def ensure_reports_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS telegram_channels (
            id           INTEGER PRIMARY KEY,
            channel_id   TEXT NOT NULL UNIQUE,
            channel_name TEXT,
            is_active    INTEGER DEFAULT 1,
            last_sync    TEXT,
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS report_files (
            id           INTEGER PRIMARY KEY,
            channel_id   TEXT,
            message_id   INTEGER,
            stock_code   TEXT,
            stock_name   TEXT,
            sector       TEXT,
            report_date  TEXT,
            file_name    TEXT,
            saved_name   TEXT,
            file_path    TEXT,
            external_url TEXT,
            file_size    INTEGER,
            mime_type    TEXT,
            caption      TEXT,
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(channel_id, message_id)
        );
        CREATE INDEX IF NOT EXISTS idx_rf_stock  ON report_files(stock_code);
        CREATE INDEX IF NOT EXISTS idx_rf_date   ON report_files(report_date);
        CREATE INDEX IF NOT EXISTS idx_rf_ch     ON report_files(channel_id);
        CREATE INDEX IF NOT EXISTS idx_rf_sector ON report_files(sector);
        """
    )

    # 기존 스키마 호환: external_url 컬럼 없으면 추가
    cols = {row[1] for row in conn.execute("PRAGMA table_info(report_files)").fetchall()}
    if "external_url" not in cols:
        conn.execute("ALTER TABLE report_files ADD COLUMN external_url TEXT")
    conn.commit()


def resolve_report_path(file_path: str | None, saved_name: str | None) -> Path | None:
    """
    기존 file_path가 유효하면 그대로 사용.
    없으면 REPORTS_DIR/saved_name fallback.
    """
    if file_path:
        p = Path(file_path)
        if p.exists():
            return p
    if saved_name:
        p2 = REPORTS_DIR / saved_name
        if p2.exists():
            return p2
    return None


def migrate_from_stock_db(source_db: Path = STOCK_DB_PATH, target_db: Path = REPORTS_DB_PATH) -> dict:
    """
    stock.db.report_files / telegram_channels -> reports_catalog.db로 1회 마이그레이션.
    """
    src = sqlite3.connect(str(source_db), timeout=60)
    dst = sqlite3.connect(str(target_db), timeout=60)
    try:
        dst.execute("PRAGMA journal_mode=WAL")
        ensure_reports_schema(dst)

        src_tbl = src.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='report_files'"
        ).fetchone()
        if not src_tbl:
            return {"migrated_report_files": 0, "migrated_channels": 0, "note": "source table missing"}

        before_rf = dst.execute("SELECT COUNT(*) FROM report_files").fetchone()[0]
        before_ch = dst.execute("SELECT COUNT(*) FROM telegram_channels").fetchone()[0]

        rf_rows = src.execute(
            """
            SELECT id, channel_id, message_id, stock_code, stock_name, sector,
                   report_date, file_name, saved_name, file_path, file_size,
                   mime_type, caption, created_at
            FROM report_files
            """
        ).fetchall()
        dst.executemany(
            """
            INSERT OR IGNORE INTO report_files
              (id, channel_id, message_id, stock_code, stock_name, sector,
               report_date, file_name, saved_name, file_path, file_size,
               mime_type, caption, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rf_rows,
        )

        ch_tbl = src.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='telegram_channels'"
        ).fetchone()
        if ch_tbl:
            ch_rows = src.execute(
                """
                SELECT id, channel_id, channel_name, is_active, last_sync, created_at
                FROM telegram_channels
                """
            ).fetchall()
            dst.executemany(
                """
                INSERT OR IGNORE INTO telegram_channels
                  (id, channel_id, channel_name, is_active, last_sync, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ch_rows,
            )
        else:
            ch_rows = []

        dst.commit()
        after_rf = dst.execute("SELECT COUNT(*) FROM report_files").fetchone()[0]
        after_ch = dst.execute("SELECT COUNT(*) FROM telegram_channels").fetchone()[0]
        return {
            "migrated_report_files": int(after_rf - before_rf),
            "migrated_channels": int(after_ch - before_ch),
            "source_rows_report_files": len(rf_rows),
            "source_rows_channels": len(ch_rows),
        }
    finally:
        src.close()
        dst.close()
