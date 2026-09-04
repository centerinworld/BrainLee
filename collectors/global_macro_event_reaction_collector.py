"""
Global macro event reaction collector.

Links economic event surprises to subsequent market/factor moves using
locally stored price history. This is intentionally DB-only so it can run
without depending on market-data APIs during dashboard refreshes.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime

DB_PATH = "stock.db"
logger = logging.getLogger(__name__)


ASSETS = [
    ("^GSPC", "S&P 500", "US equity"),
    ("^IXIC", "Nasdaq Composite", "US equity"),
    ("^SOX", "Philadelphia Semiconductor", "US semiconductor"),
    ("^VIX", "VIX", "US volatility"),
    ("^TNX", "US 10Y Yield", "US rates"),
    ("^KS11", "KOSPI", "Korea equity"),
    ("^KQ11", "KOSDAQ", "Korea equity"),
    ("^KS200", "KOSPI 200", "Korea equity"),
]
WINDOWS = [("1D", 1), ("5D", 5)]


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS global_macro_event_reactions (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id       INTEGER NOT NULL,
            event_date     TEXT NOT NULL,
            country        TEXT,
            indicator_code TEXT,
            event_name     TEXT,
            surprise_value REAL,
            surprise_pct   REAL,
            asset_code     TEXT NOT NULL,
            asset_name     TEXT,
            asset_group    TEXT,
            "window"       TEXT NOT NULL,
            base_date      TEXT,
            end_date       TEXT,
            base_close     REAL,
            end_close      REAL,
            return_pct     REAL,
            direction      TEXT,
            impact_score   REAL,
            created_at     TEXT DEFAULT (datetime('now')),
            UNIQUE(event_id, asset_code, "window")
        );
        CREATE INDEX IF NOT EXISTS idx_gmer_event ON global_macro_event_reactions(event_id);
        CREATE INDEX IF NOT EXISTS idx_gmer_asset ON global_macro_event_reactions(asset_code, "window");
    """)
    conn.commit()


def _load_prices(conn: sqlite3.Connection, asset_code: str) -> list[sqlite3.Row]:
    return conn.execute("""
        SELECT date, close
        FROM price_history
        WHERE stock_code = ?
          AND close IS NOT NULL
          AND close > 0
        ORDER BY date
    """, (asset_code,)).fetchall()


def _event_base_index(rows: list[sqlite3.Row], event_date: str) -> int | None:
    for idx, row in enumerate(rows):
        if str(row["date"])[:10] >= event_date:
            return idx
    return None


def _impact_score(return_pct: float | None, surprise_pct: float | None, surprise_value: float | None) -> float | None:
    if return_pct is None:
        return None
    surprise_mag = abs(surprise_pct) if surprise_pct is not None else abs(surprise_value or 0.0)
    surprise_weight = min(surprise_mag, 10.0) / 10.0
    return round(abs(return_pct) * (0.5 + surprise_weight), 4)


def _upsert_reaction(
    conn: sqlite3.Connection,
    event: sqlite3.Row,
    asset_code: str,
    asset_name: str,
    asset_group: str,
    window: str,
    base_row: sqlite3.Row,
    end_row: sqlite3.Row,
) -> int:
    base_close = float(base_row["close"])
    end_close = float(end_row["close"])
    return_pct = ((end_close / base_close) - 1.0) * 100.0 if base_close else None
    direction = "up" if return_pct and return_pct > 0 else "down" if return_pct and return_pct < 0 else "flat"
    impact_score = _impact_score(return_pct, event["surprise_pct"], event["surprise_value"])
    # 2026-08-23: window는 PostgreSQL 예약어라 컬럼 목록/ON CONFLICT에서 따옴표 없이
    # 쓰면 "syntax error at or near window"로 실패(DDL은 db_compat이 자동으로 따옴표를
    # 붙여줘서 테이블/인덱스 생성은 이미 성공한 상태였음 — INSERT/ON CONFLICT만 수동 처리 필요).
    conn.execute("""
        INSERT INTO global_macro_event_reactions (
            event_id, event_date, country, indicator_code, event_name,
            surprise_value, surprise_pct, asset_code, asset_name, asset_group,
            "window", base_date, end_date, base_close, end_close, return_pct,
            direction, impact_score, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(event_id, asset_code, "window") DO UPDATE SET
            event_date=excluded.event_date,
            country=excluded.country,
            indicator_code=excluded.indicator_code,
            event_name=excluded.event_name,
            surprise_value=excluded.surprise_value,
            surprise_pct=excluded.surprise_pct,
            asset_name=excluded.asset_name,
            asset_group=excluded.asset_group,
            base_date=excluded.base_date,
            end_date=excluded.end_date,
            base_close=excluded.base_close,
            end_close=excluded.end_close,
            return_pct=excluded.return_pct,
            direction=excluded.direction,
            impact_score=excluded.impact_score,
            created_at=excluded.created_at
    """, (
        event["id"], event["event_date"], event["country"], event["indicator_code"],
        event["event_name"], event["surprise_value"], event["surprise_pct"],
        asset_code, asset_name, asset_group, "window",
        str(base_row["date"])[:10], str(end_row["date"])[:10],
        base_close, end_close, return_pct, direction, impact_score,
    ))
    return 1


def collect_global_macro_event_reactions() -> int:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    _ensure_table(conn)
    today = datetime.now().strftime("%Y-%m-%d")
    events = conn.execute("""
        SELECT id, event_date, country, indicator_code, event_name,
               surprise_value, surprise_pct, actual
        FROM global_macro_events
        WHERE event_date <= ?
          AND actual IS NOT NULL
        ORDER BY event_date
    """, (today,)).fetchall()
    prices = {asset_code: _load_prices(conn, asset_code) for asset_code, _, _ in ASSETS}

    total = 0
    for event in events:
        for asset_code, asset_name, asset_group in ASSETS:
            rows = prices.get(asset_code) or []
            base_idx = _event_base_index(rows, event["event_date"])
            if base_idx is None:
                continue
            for window, offset in WINDOWS:
                end_idx = base_idx + offset
                if end_idx >= len(rows):
                    continue
                total += _upsert_reaction(
                    conn, event, asset_code, asset_name, asset_group, window,
                    rows[base_idx], rows[end_idx]
                )
    conn.execute("""
        INSERT INTO global_macro_collection_log (source, status, records, message)
        VALUES ('event_reactions', ?, ?, ?)
    """, ("ok", total, "linked macro events to local market/factor reactions"))
    conn.commit()
    conn.close()
    logger.info("Global macro event reactions collected %s records", total)
    return total


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    n = collect_global_macro_event_reactions()
    print(f"Global macro event reactions collected {n} records")
