#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import telegram_collector as collector
from routes.cherry_screener import refresh_cherry_screener_cache
from routes.company_intelligence import compare_portfolio_holdings

DB_PATH = ROOT / "stock.db"

CHERRY_FAMILY_CHANNELS = [
    {
        "channel_id": "체리형부 25.4Q [25.11.01 ~ 26.1.31]",
        "channel_name": "체리형부 25.4Q [25.11.01 ~ 26.1.31]",
        "entity_hint": None,
        "collect": False,
    },
    {
        "channel_id": "체리형부 26.1Q [26.02.02 ~ 26.05.30]",
        "channel_name": "체리형부 26.1Q [26.02.02 ~ 26.05.30]",
        "entity_hint": None,
        "collect": False,
    },
    {
        "channel_id": "체리형부 채널 26.06.01~08.31",
        "channel_name": "체리형부 채널 26.06.01~08.31",
        "entity_hint": "-1003907826971",
        "collect": True,
    },
    {
        "channel_id": "Valuefs1",
        "channel_name": "적절한 지식과 검증된 판단, 체리형부",
        "entity_hint": "@Valuefs1",
        "collect": True,
    },
]


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=120000")
    return conn


def _ensure_learning_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cherry_family_learning_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_type TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            channels_total INTEGER DEFAULT 0,
            channels_active INTEGER DEFAULT 0,
            telegram_message_count INTEGER DEFAULT 0,
            report_file_count INTEGER DEFAULT 0,
            screener_universe_scanned INTEGER DEFAULT 0,
            three_screen_count INTEGER DEFAULT 0,
            two_screen_count INTEGER DEFAULT 0,
            portfolio_profile_count INTEGER DEFAULT 0,
            auto_candidate_count INTEGER DEFAULT 0,
            notes_json TEXT
        )
        """
    )
    conn.commit()


def _start_run(conn: sqlite3.Connection, run_type: str) -> int:
    _ensure_learning_table(conn)
    started_at = datetime.now().isoformat()
    cur = conn.execute(
        """
        INSERT INTO cherry_family_learning_runs (run_type, status, started_at)
        VALUES (?, 'running', ?)
        """,
        (run_type, started_at),
    )
    conn.commit()
    return int(cur.lastrowid)


def _finish_run(conn: sqlite3.Connection, run_id: int, status: str, payload: dict[str, Any]) -> None:
    conn.execute(
        """
        UPDATE cherry_family_learning_runs
        SET status=?,
            completed_at=?,
            channels_total=?,
            channels_active=?,
            telegram_message_count=?,
            report_file_count=?,
            screener_universe_scanned=?,
            three_screen_count=?,
            two_screen_count=?,
            portfolio_profile_count=?,
            auto_candidate_count=?,
            notes_json=?
        WHERE id=?
        """,
        (
            status,
            datetime.now().isoformat(),
            payload.get("channels_total", 0),
            payload.get("channels_active", 0),
            payload.get("telegram_message_count", 0),
            payload.get("report_file_count", 0),
            payload.get("screener_universe_scanned", 0),
            payload.get("three_screen_count", 0),
            payload.get("two_screen_count", 0),
            payload.get("portfolio_profile_count", 0),
            payload.get("auto_candidate_count", 0),
            json.dumps(payload.get("notes", {}), ensure_ascii=False),
            run_id,
        ),
    )
    conn.commit()


def _register_family_channels(conn: sqlite3.Connection) -> None:
    collector.init_db(conn)
    collector._ensure_entity_hint_column(conn)
    for item in CHERRY_FAMILY_CHANNELS:
        collector.add_channel(
            conn,
            channel_id=item["channel_id"],
            name=item["channel_name"],
            entity_hint=item.get("entity_hint"),
        )


def _family_counts(conn: sqlite3.Connection) -> dict[str, Any]:
    channel_rows = conn.execute(
        """
        SELECT channel_id, channel_name, entity_hint, is_active, last_sync
        FROM telegram_channels
        WHERE channel_id IN ({placeholders})
        ORDER BY channel_name, channel_id
        """.format(placeholders=",".join("?" for _ in CHERRY_FAMILY_CHANNELS)),
        [item["channel_id"] for item in CHERRY_FAMILY_CHANNELS],
    ).fetchall()
    message_total = 0
    report_total = 0
    channel_summaries = []
    for row in channel_rows:
        channel_id = row["channel_id"]
        msg_count = conn.execute(
            "SELECT COUNT(*) FROM telegram_messages WHERE channel=?",
            (channel_id,),
        ).fetchone()[0]
        report_count = conn.execute(
            "SELECT COUNT(*) FROM report_files WHERE channel_id=?",
            (channel_id,),
        ).fetchone()[0]
        message_total += int(msg_count or 0)
        report_total += int(report_count or 0)
        channel_summaries.append(
            {
                "channel_id": channel_id,
                "channel_name": row["channel_name"] or channel_id,
                "entity_hint": row["entity_hint"],
                "is_active": bool(row["is_active"]),
                "last_sync": row["last_sync"],
                "telegram_messages": int(msg_count or 0),
                "report_files": int(report_count or 0),
            }
        )
    return {
        "channels_total": len(CHERRY_FAMILY_CHANNELS),
        "channels_active": len([row for row in channel_rows if row["is_active"]]),
        "telegram_message_count": message_total,
        "report_file_count": report_total,
        "channels": channel_summaries,
    }


async def _run_collection(limit: int, since_days: int | None) -> list[dict[str, Any]]:
    targets = [
        {
            "channel_id": item["channel_id"],
            "channel_name": item["channel_name"],
            "entity_hint": item.get("entity_hint"),
        }
        for item in CHERRY_FAMILY_CHANNELS
        if item.get("collect")
    ]
    if not targets:
        return []
    await collector.run_collect(targets, limit=limit, since_days=since_days)
    return targets


def main() -> int:
    parser = argparse.ArgumentParser(description="체리형부 family 수집·재학습 파이프라인")
    parser.add_argument("--run-type", default="scheduled", help="manual / scheduled / catchup")
    parser.add_argument("--skip-collect", action="store_true", help="텔레그램 증분 수집 생략")
    parser.add_argument("--include-auto-board", action="store_true", help="자동 후보 리스트까지 함께 재계산")
    parser.add_argument("--limit", type=int, default=1500, help="수집 대상 채널당 최대 메시지 수")
    parser.add_argument("--days", type=int, default=7, help="최근 N일 수집")
    args = parser.parse_args()

    conn = _connect()
    run_id = _start_run(conn, args.run_type)
    try:
        _register_family_channels(conn)
        collected_targets: list[dict[str, Any]] = []
        if not args.skip_collect:
            collected_targets = asyncio.run(_run_collection(limit=args.limit, since_days=args.days))

        screener = refresh_cherry_screener_cache()
        portfolio_compare = compare_portfolio_holdings(limit=12)
        auto_board = None
        if args.include_auto_board:
            from routes.buy_candidates import _build_auto_board

            auto_board = _build_auto_board()
        counts = _family_counts(conn)
        payload = {
            **counts,
            "screener_universe_scanned": int(screener.get("universe_scanned") or 0),
            "three_screen_count": len(screener.get("three_screen_pass") or []),
            "two_screen_count": len(screener.get("two_screen_pass") or []),
            "portfolio_profile_count": len(portfolio_compare.get("items") or []),
            "auto_candidate_count": len((auto_board or {}).get("items") or []),
            "notes": {
                "collected_targets": collected_targets,
                "portfolio_as_of": portfolio_compare.get("as_of"),
                "auto_board_as_of": (auto_board or {}).get("as_of"),
                "auto_board_included": bool(args.include_auto_board),
                "screener_generated_at": screener.get("generated_at"),
            },
        }
        _finish_run(conn, run_id, "completed", payload)
        print(json.dumps({"ok": True, "run_id": run_id, **payload}, ensure_ascii=False))
        return 0
    except Exception as exc:
        counts = _family_counts(conn)
        _finish_run(
            conn,
            run_id,
            "failed",
            {
                **counts,
                "notes": {
                    "error": str(exc),
                },
            },
        )
        print(json.dumps({"ok": False, "run_id": run_id, "error": str(exc)}, ensure_ascii=False))
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
