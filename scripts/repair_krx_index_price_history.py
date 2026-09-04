#!/usr/bin/env python3
"""Repair KRX index rows in price_history from the official KRX API.

This script only touches index symbols:
  ^KS11, ^KQ11, ^KS200, ^KQ150

It first copies existing rows for the requested period to a timestamped backup
table, then upserts official KRX OHLCV values.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import collect_krx_history as krx


DB_PATH = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"
INDEX_SYMBOLS = ("^KS11", "^KQ11", "^KS200", "^KQ150")
INDEX_PATHS = ("idx/kospi_dd_trd", "idx/kosdaq_dd_trd")
INDEX_EXACT_MAP = {
    "코스피": "^KS11",
    "KOSPI": "^KS11",
    "코스닥": "^KQ11",
    "KOSDAQ": "^KQ11",
    "코스피 200": "^KS200",
    "코스닥 150": "^KQ150",
}


def trading_days(start: str, end: str) -> list[str]:
    cur = datetime.strptime(start, "%Y-%m-%d").date()
    end_d = datetime.strptime(end, "%Y-%m-%d").date()
    days = []
    while cur <= end_d:
        if cur.weekday() < 5:
            days.append(cur.isoformat())
        cur += timedelta(days=1)
    return days


def backup_existing(conn: sqlite3.Connection, start: str, end: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    table = f"price_history_index_backup_{stamp}"
    symbols = ",".join("?" for _ in INDEX_SYMBOLS)
    conn.execute(
        f"""
        CREATE TABLE {table} AS
        SELECT *
        FROM price_history
        WHERE stock_code IN ({symbols})
          AND date BETWEEN ? AND ?
        """,
        (*INDEX_SYMBOLS, start, end),
    )
    conn.commit()
    return table


def num(row: dict, key: str) -> float:
    v = row.get(key, "")
    try:
        return float(str(v).replace(",", "")) if v not in ("", "-", None) else 0.0
    except Exception:
        return 0.0


def save_official_index_rows(conn: sqlite3.Connection, rows: list[dict], day: str) -> int:
    saved = 0
    for row in rows:
        code = INDEX_EXACT_MAP.get(str(row.get("IDX_NM", "")).strip())
        if not code:
            continue
        close = num(row, "CLSPRC_IDX")
        if close <= 0:
            continue
        vals = (
            code,
            day,
            num(row, "OPNPRC_IDX"),
            num(row, "HGPRC_IDX"),
            num(row, "LWPRC_IDX"),
            close,
            num(row, "ACC_TRDVOL"),
            num(row, "ACC_TRDVAL"),
        )
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO price_history
                (stock_code, date, open, high, low, close, volume, trade_amount)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            vals,
        )
        if cur.rowcount == 0:
            conn.execute(
                """
                UPDATE price_history
                SET open=?, high=?, low=?, close=?, volume=?, trade_amount=?
                WHERE stock_code=? AND date=?
                """,
                (vals[2], vals[3], vals[4], vals[5], vals[6], vals[7], vals[0], vals[1]),
            )
        saved += 1
    return saved


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end", default=datetime.now().date().isoformat())
    parser.add_argument("--sleep", type=float, default=0.08)
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH, timeout=120)
    backup_table = backup_existing(conn, args.start, args.end)

    saved = 0
    days_with_data = 0
    skipped = 0
    for day in trading_days(args.start, args.end):
        day_saved = 0
        for path in INDEX_PATHS:
            rows = krx._fetch(path, day)
            if not rows:
                continue
            day_saved += save_official_index_rows(conn, rows, day)
            time.sleep(args.sleep)
        if day_saved:
            days_with_data += 1
            saved += day_saved
            conn.commit()
        else:
            skipped += 1

    official_days = set(
        r[0]
        for r in conn.execute(
            """
            SELECT DISTINCT date
            FROM price_history
            WHERE stock_code='^KS11' AND date BETWEEN ? AND ?
              AND trade_amount > 0
            """,
            (args.start, args.end),
        ).fetchall()
    )
    requested_weekdays = set(trading_days(args.start, args.end))
    unofficial_weekdays = sorted(requested_weekdays - official_days)
    if unofficial_weekdays:
        ph = ",".join("?" for _ in unofficial_weekdays)
        sym = ",".join("?" for _ in INDEX_SYMBOLS)
        conn.execute(
            f"""
            DELETE FROM price_history
            WHERE stock_code IN ({sym}) AND date IN ({ph})
            """,
            (*INDEX_SYMBOLS, *unofficial_weekdays),
        )
        conn.commit()

    check = conn.execute(
        """
        SELECT stock_code, MIN(date), MAX(date), COUNT(*),
               SUM(CASE WHEN close<=0 OR open<=0 OR high<=0 OR low<=0 THEN 1 ELSE 0 END)
        FROM price_history
        WHERE stock_code IN ('^KS11','^KQ11','^KS200','^KQ150')
          AND date BETWEEN ? AND ?
        GROUP BY stock_code
        ORDER BY stock_code
        """,
        (args.start, args.end),
    ).fetchall()
    conn.close()

    print(f"backup_table={backup_table}")
    print(f"saved_or_updated={saved} days_with_data={days_with_data} skipped_weekdays_no_data={skipped}")
    for row in check:
        print(row)


if __name__ == "__main__":
    main()
