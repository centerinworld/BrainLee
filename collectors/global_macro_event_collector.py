"""
Global macro event calendar collector.

Seeds the Week 5 economic calendar from official scheduled releases and links
available actual/previous values from global_macro_data.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta

from config import IS_POSTGRES

logger = logging.getLogger(__name__)
DB_PATH = "stock.db"


FOMC_2026 = [
    ("2026-01-28", "14:00", "FOMC rate decision", "US_FED_RATE", 3),
    ("2026-03-18", "14:00", "FOMC rate decision + projections", "US_FED_RATE", 3),
    ("2026-04-29", "14:00", "FOMC rate decision", "US_FED_RATE", 3),
    ("2026-06-17", "14:00", "FOMC rate decision + projections", "US_FED_RATE", 3),
    ("2026-07-29", "14:00", "FOMC rate decision", "US_FED_RATE", 3),
    ("2026-09-16", "14:00", "FOMC rate decision + projections", "US_FED_RATE", 3),
    ("2026-10-28", "14:00", "FOMC rate decision", "US_FED_RATE", 3),
    ("2026-12-09", "14:00", "FOMC rate decision + projections", "US_FED_RATE", 3),
]

CPI_2026 = [
    ("2026-02-13", "08:30", "US CPI release for January 2026", "US_CPI", 3),
    ("2026-03-11", "08:30", "US CPI release for February 2026", "US_CPI", 3),
    ("2026-04-10", "08:30", "US CPI release for March 2026", "US_CPI", 3),
    ("2026-05-12", "08:30", "US CPI release for April 2026", "US_CPI", 3),
    ("2026-06-10", "08:30", "US CPI release for May 2026", "US_CPI", 3),
    ("2026-07-14", "08:30", "US CPI release for June 2026", "US_CPI", 3),
    ("2026-08-12", "08:30", "US CPI release for July 2026", "US_CPI", 3),
    ("2026-09-11", "08:30", "US CPI release for August 2026", "US_CPI", 3),
    ("2026-10-14", "08:30", "US CPI release for September 2026", "US_CPI", 3),
    ("2026-11-10", "08:30", "US CPI release for October 2026", "US_CPI", 3),
    ("2026-12-10", "08:30", "US CPI release for November 2026", "US_CPI", 3),
]

EMPLOYMENT_2026 = [
    ("2026-02-11", "08:30", "US Employment Situation for January 2026", "US_UNEMPLOYMENT", 3),
    ("2026-03-06", "08:30", "US Employment Situation for February 2026", "US_UNEMPLOYMENT", 3),
    ("2026-04-03", "08:30", "US Employment Situation for March 2026", "US_UNEMPLOYMENT", 3),
    ("2026-05-08", "08:30", "US Employment Situation for April 2026", "US_UNEMPLOYMENT", 3),
    ("2026-06-05", "08:30", "US Employment Situation for May 2026", "US_UNEMPLOYMENT", 3),
    ("2026-07-02", "08:30", "US Employment Situation for June 2026", "US_UNEMPLOYMENT", 3),
    ("2026-08-07", "08:30", "US Employment Situation for July 2026", "US_UNEMPLOYMENT", 3),
    ("2026-09-04", "08:30", "US Employment Situation for August 2026", "US_UNEMPLOYMENT", 3),
    ("2026-10-02", "08:30", "US Employment Situation for September 2026", "US_UNEMPLOYMENT", 3),
    ("2026-11-06", "08:30", "US Employment Situation for October 2026", "US_UNEMPLOYMENT", 3),
    ("2026-12-04", "08:30", "US Employment Situation for November 2026", "US_UNEMPLOYMENT", 3),
]


def _ensure_event_columns(conn: sqlite3.Connection) -> None:
    # The PostgreSQL schema is migrated centrally. Failed ALTER TABLE statements
    # leave the transaction aborted, preventing event-calendar upserts.
    if IS_POSTGRES:
        return
    for ddl in [
        "ALTER TABLE global_macro_events ADD COLUMN surprise_value REAL",
        "ALTER TABLE global_macro_events ADD COLUMN surprise_pct REAL",
        "ALTER TABLE global_macro_events ADD COLUMN surprise_basis TEXT",
        "ALTER TABLE global_macro_events ADD COLUMN status TEXT DEFAULT 'scheduled'",
        "ALTER TABLE global_macro_events ADD COLUMN source TEXT",
        "ALTER TABLE global_macro_events ADD COLUMN updated_at TEXT",
    ]:
        try:
            conn.execute(ddl)
        except Exception as exc:
            # 2026-08-14: SQLite는 "duplicate column name"을, PostgreSQL은
            # "column ... already exists"를 반환 — db_compat.py가 psycopg
            # 원본 예외를 그대로 재전파하므로 sqlite3.OperationalError만
            # 잡던 기존 코드는 PostgreSQL 라우팅 상태에서 매번 실패하고
            # 있었음(글로벌매크로수집 잡 5일 연속 실패로 발견).
            msg = str(exc).lower()
            if "duplicate column" not in msg and "already exists" not in msg:
                raise
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_gme_unique_event
        ON global_macro_events(event_date, COALESCE(country,''), COALESCE(indicator_code,''), event_name)
    """)


def _series_values(conn: sqlite3.Connection, indicator_code: str, event_date: str) -> tuple[float | None, float | None, str | None]:
    rows = conn.execute("""
        SELECT date, value
        FROM global_macro_data
        WHERE indicator_code = ? AND value IS NOT NULL AND date <= ?
        ORDER BY date DESC
        LIMIT 2
    """, (indicator_code, event_date)).fetchall()
    if not rows:
        latest = conn.execute("""
            SELECT date, value
            FROM global_macro_data
            WHERE indicator_code = ? AND value IS NOT NULL
            ORDER BY date DESC
            LIMIT 1
        """, (indicator_code,)).fetchone()
        return None, latest["value"] if latest else None, latest["date"] if latest else None
    actual = rows[0]["value"]
    previous = rows[1]["value"] if len(rows) > 1 else None
    return actual, previous, rows[0]["date"]


def _surprise(actual: float | None, forecast: float | None, previous: float | None) -> tuple[float | None, float | None, str | None]:
    baseline = forecast if forecast is not None else previous
    basis = "forecast" if forecast is not None else "previous" if previous is not None else None
    if actual is None or baseline is None:
        return None, None, basis
    surprise_value = actual - baseline
    surprise_pct = (surprise_value / abs(baseline) * 100.0) if baseline else None
    return surprise_value, surprise_pct, basis


def _upsert_event(
    conn: sqlite3.Connection,
    event_date: str,
    event_time: str,
    country: str,
    event_name: str,
    indicator_code: str,
    importance: int,
    source: str,
) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    actual, previous, actual_date = _series_values(conn, indicator_code, event_date)
    if event_date > today:
        actual = None
    surprise_value, surprise_pct, surprise_basis = _surprise(actual, None, previous)
    status = "actual" if actual is not None else "scheduled"
    unit_row = conn.execute(
        "SELECT unit FROM global_macro_categories WHERE code = ?",
        (indicator_code,),
    ).fetchone()
    unit = unit_row["unit"] if unit_row else None
    cursor = conn.execute("""
        INSERT INTO global_macro_events (
            event_date, event_time, country, indicator_code, event_name, importance,
            forecast, previous, actual, unit, surprise_value, surprise_pct,
            surprise_basis, status, source, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(event_date, COALESCE(country,''), COALESCE(indicator_code,''), event_name)
        DO UPDATE SET
            event_time=excluded.event_time,
            importance=excluded.importance,
            previous=excluded.previous,
            actual=excluded.actual,
            unit=excluded.unit,
            surprise_value=excluded.surprise_value,
            surprise_pct=excluded.surprise_pct,
            surprise_basis=excluded.surprise_basis,
            status=excluded.status,
            source=excluded.source,
            updated_at=excluded.updated_at
    """, (
        event_date, event_time, country, indicator_code, event_name, importance,
        previous, actual, unit, surprise_value, surprise_pct, surprise_basis, status, source,
    ))
    # Cursor rowcount works for both sqlite3 and the PostgreSQL compatibility layer.
    return max(0, int(cursor.rowcount or 0))


def collect_global_macro_events() -> int:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _ensure_event_columns(conn)
    total = 0
    events = [
        *[(d, t, "US", name, code, imp, "official_fed_fomc") for d, t, name, code, imp in FOMC_2026],
        *[(d, t, "US", name, code, imp, "official_bls_cpi") for d, t, name, code, imp in CPI_2026],
        *[(d, t, "US", name, code, imp, "official_bls_employment") for d, t, name, code, imp in EMPLOYMENT_2026],
    ]
    for event_date, event_time, country, event_name, indicator_code, importance, source in events:
        total += _upsert_event(
            conn, event_date, event_time, country, event_name, indicator_code, importance, source
        )
    _log(conn, total, "ok", "seeded Fed FOMC and BLS CPI/employment release calendar")
    conn.commit()
    conn.close()
    logger.info("Global macro events collected %s records", total)
    return total


def _log(conn: sqlite3.Connection, records: int, status: str = "ok", msg: str = "") -> None:
    conn.execute("""
        INSERT INTO global_macro_collection_log (source, status, records, message)
        VALUES ('events', ?, ?, ?)
    """, (status, records, msg))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    n = collect_global_macro_events()
    print(f"글로벌 경제 이벤트 수집 완료: {n}건")
