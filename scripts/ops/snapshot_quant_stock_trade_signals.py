#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "stock.db"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from routes.cafe_signals import get_stock_trade_signals


def main() -> None:
    payload = get_stock_trade_signals(limit=100)
    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("PRAGMA busy_timeout=120000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS quant_stock_trade_signal_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_date TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT NOT NULL,
            action TEXT NOT NULL,
            score REAL NOT NULL,
            positive_drivers INTEGER DEFAULT 0,
            negative_drivers INTEGER DEFAULT 0,
            drivers_json TEXT,
            policy TEXT,
            generated_at TEXT NOT NULL,
            UNIQUE(signal_date, stock_code)
        )
        """
    )
    now = datetime.now()
    signal_date = now.strftime("%Y-%m-%d")
    generated_at = now.strftime("%Y-%m-%d %H:%M:%S")
    for item in payload.get("items") or []:
        conn.execute(
            """
            INSERT INTO quant_stock_trade_signal_snapshots
                (signal_date, stock_code, stock_name, action, score,
                 positive_drivers, negative_drivers, drivers_json, policy, generated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(signal_date, stock_code) DO UPDATE SET
                stock_name=excluded.stock_name,
                action=excluded.action,
                score=excluded.score,
                positive_drivers=excluded.positive_drivers,
                negative_drivers=excluded.negative_drivers,
                drivers_json=excluded.drivers_json,
                policy=excluded.policy,
                generated_at=excluded.generated_at
            """,
            (
                signal_date,
                item["stock_code"],
                item["stock_name"],
                item["action"],
                item["score"],
                item["positive_drivers"],
                item["negative_drivers"],
                json.dumps(item["drivers"], ensure_ascii=False),
                payload.get("policy"),
                generated_at,
            ),
        )
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) FROM quant_stock_trade_signal_snapshots WHERE signal_date=?", (signal_date,)
    ).fetchone()[0]
    conn.close()
    print(json.dumps({"signal_date": signal_date, "snapshots": count, "counts": payload.get("counts")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
