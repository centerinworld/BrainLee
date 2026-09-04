"""Register immutable strategy signals and update leakage-safe forward outcomes."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime

from db_utils import connect_stock_db


DDL = """
CREATE TABLE IF NOT EXISTS live_signal_registry(
 signal_id TEXT PRIMARY KEY,stock_code TEXT NOT NULL,signal_type TEXT NOT NULL,strategy_id TEXT,
 signal_date TEXT NOT NULL,available_at TEXT NOT NULL,entry_date TEXT,entry_price REAL,price_basis TEXT NOT NULL,
 quality_score REAL,confidence_score REAL,action TEXT NOT NULL,signal_payload_json TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS live_signal_outcomes(
 signal_id TEXT NOT NULL,horizon_days INTEGER NOT NULL,outcome_date TEXT,outcome_price REAL,return_pct REAL,
 max_gain_pct REAL,max_loss_pct REAL,status TEXT NOT NULL,updated_at TEXT NOT NULL,
 PRIMARY KEY(signal_id,horizon_days),FOREIGN KEY(signal_id) REFERENCES live_signal_registry(signal_id));
"""
HORIZONS = (1, 5, 20, 60, 120, 252)


def ensure(conn) -> None:
    conn.executescript(DDL)


def _signal_id(signal_type: str, stock_code: str, signal_date: str, strategy_id: str | None) -> str:
    identity = f"{signal_type}|{stock_code}|{signal_date}|{strategy_id or ''}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return f"{signal_type}:{stock_code}:{signal_date}:{digest}"


def _usable_prices(conn, stock_code: str, start_date: str, *, strictly_after: bool) -> list:
    operator = ">" if strictly_after else ">="
    return conn.execute(
        f"""SELECT p.date,p.close,p.open
            FROM price_history p
            LEFT JOIN price_jump_audit a
              ON a.stock_code=p.stock_code AND a.event_date=substr(p.date,1,10)
            WHERE p.stock_code=? AND p.date{operator}? AND p.close>0
              AND COALESCE(a.return_usable,1)=1
            ORDER BY p.date""",
        (stock_code, start_date),
    ).fetchall()


def register_signal(
    *,
    stock_code: str,
    signal_type: str,
    signal_date: str,
    available_at: str,
    action: str,
    payload: dict,
    strategy_id: str | None = None,
    quality_score: float | None = None,
    confidence_score: float | None = None,
    conn=None,
) -> str:
    owned = conn is None
    conn = conn or connect_stock_db()
    ensure(conn)
    signal_id = _signal_id(signal_type, stock_code, signal_date, strategy_id)
    now = datetime.now().isoformat(timespec="seconds")
    entry = _usable_prices(conn, stock_code, signal_date, strictly_after=True)
    first_entry = entry[0] if entry else None
    cursor = conn.execute(
        """INSERT INTO live_signal_registry VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(signal_id) DO NOTHING""",
        (
            signal_id, stock_code, signal_type, strategy_id, signal_date, available_at,
            first_entry[0] if first_entry else None, first_entry[2] if first_entry else None,
            "price_history+price_jump_audit", quality_score, confidence_score, action,
            json.dumps(payload, ensure_ascii=False), now,
        ),
    )
    inserted = cursor.rowcount > 0
    if inserted:
        conn.executemany(
            """INSERT INTO live_signal_outcomes(signal_id,horizon_days,status,updated_at)
               VALUES(?,?,?,?) ON CONFLICT(signal_id,horizon_days) DO NOTHING""",
            [(signal_id, horizon, "pending", now) for horizon in HORIZONS],
        )
    if owned:
        conn.commit()
        conn.close()
    return signal_id


def update_outcomes(conn=None) -> int:
    owned = conn is None
    conn = conn or connect_stock_db()
    conn.row_factory = sqlite3.Row
    ensure(conn)
    now = datetime.now().isoformat(timespec="seconds")
    updated = 0
    signals = conn.execute("SELECT * FROM live_signal_registry").fetchall()
    for signal in signals:
        entry_date = signal["entry_date"]
        entry_price = signal["entry_price"]
        if not entry_date or not entry_price:
            entries = _usable_prices(conn, signal["stock_code"], signal["signal_date"], strictly_after=True)
            if not entries:
                continue
            entry_date, entry_price = entries[0][0], float(entries[0][2])
            conn.execute(
                "UPDATE live_signal_registry SET entry_date=?,entry_price=? WHERE signal_id=?",
                (entry_date, entry_price, signal["signal_id"]),
            )
        future = _usable_prices(conn, signal["stock_code"], entry_date, strictly_after=False)
        for horizon in HORIZONS:
            if len(future) <= horizon:
                continue
            window = future[:horizon + 1]
            end = window[-1]
            entry_price = float(entry_price)
            closes = [float(row[1]) for row in window]
            cursor = conn.execute(
                """UPDATE live_signal_outcomes
                   SET outcome_date=?,outcome_price=?,return_pct=?,max_gain_pct=?,max_loss_pct=?,
                       status='complete',updated_at=?
                   WHERE signal_id=? AND horizon_days=? AND status<>'complete'""",
                (
                    end[0], end[1], (float(end[1]) / entry_price - 1) * 100,
                    (max(closes) / entry_price - 1) * 100,
                    (min(closes) / entry_price - 1) * 100,
                    now, signal["signal_id"], horizon,
                ),
            )
            updated += max(cursor.rowcount, 0)
    if owned:
        conn.commit()
        conn.close()
    return updated
