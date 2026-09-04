"""telegram_stock_dedup.py — stock-level Telegram alert deduplication.

Some alert jobs run every day, but the user wants stock discovery alerts to be
sent only when a stock is newly detected for that alert namespace.  This module
keeps durable per-stock state in SQLite so date-based notification keys cannot
re-send the same stock every morning.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable

DB_PATH = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db")


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS telegram_stock_alert_state (
            alert_namespace TEXT NOT NULL,
            stock_code      TEXT NOT NULL,
            stock_name      TEXT,
            first_sent_at   TEXT NOT NULL,
            last_seen_at    TEXT NOT NULL,
            sent_count      INTEGER NOT NULL DEFAULT 1,
            last_payload    TEXT,
            PRIMARY KEY (alert_namespace, stock_code)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_telegram_stock_alert_state_seen "
        "ON telegram_stock_alert_state(alert_namespace, last_seen_at)"
    )


def load_sent_codes(namespace: str, db_path: str | Path = DB_PATH) -> set[str]:
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        ensure_table(conn)
        rows = conn.execute(
            "SELECT stock_code FROM telegram_stock_alert_state WHERE alert_namespace=?",
            (namespace,),
        ).fetchall()
        return {str(r[0]) for r in rows if r[0]}
    finally:
        conn.close()


def filter_new(items: Iterable[dict], namespace: str, code_key: str = "stock_code", db_path: str | Path = DB_PATH) -> list[dict]:
    sent = load_sent_codes(namespace, db_path=db_path)
    return [item for item in items if str(item.get(code_key) or "").strip() not in sent]


def mark_sent(
    namespace: str,
    items: Iterable[dict],
    code_key: str = "stock_code",
    name_key: str = "stock_name",
    payload_key: str | None = None,
    db_path: str | Path = DB_PATH,
) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        ensure_table(conn)
        count = 0
        for item in items:
            code = str(item.get(code_key) or "").strip()
            if not code:
                continue
            name = str(item.get(name_key) or "").strip() or code
            payload = str(item.get(payload_key) or "") if payload_key else ""
            conn.execute(
                """
                INSERT INTO telegram_stock_alert_state
                    (alert_namespace, stock_code, stock_name, first_sent_at, last_seen_at, sent_count, last_payload)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(alert_namespace, stock_code) DO UPDATE SET
                    stock_name=excluded.stock_name,
                    last_seen_at=excluded.last_seen_at,
                    sent_count=telegram_stock_alert_state.sent_count + 1,
                    last_payload=excluded.last_payload
                """,
                (namespace, code, name, now, now, payload),
            )
            count += 1
        conn.commit()
        return count
    finally:
        conn.close()
