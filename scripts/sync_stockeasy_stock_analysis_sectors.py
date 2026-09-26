from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db

DB_PATH = ROOT / "stock.db"
URL = "https://stockeasy.intellio.kr/stock-analysis"
API_URL = "https://stockeasy.intellio.kr/stockdata/api/v1/valuation/data"

ROW_RE = re.compile(
    r'\\\"stockCode\\\":\\\"(\d{6})\\\",\\\"stockName\\\":\\\"([^\\\"]+)\\\",\\\"industry\\\":\\\"([^\\\"]+)\\\",\\\"middleCategory\\\":\\\"([^\\\"]*)\\\"'
)


def fetch_html() -> str:
    r = requests.get(
        URL,
        timeout=30,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    r.raise_for_status()
    return r.text


def parse_rows(html: str) -> list[tuple[str, str, str, str]]:
    rows = ROW_RE.findall(html)
    # (code, name) 기준으로 마지막 값 우선
    dedup = {}
    for code, name, major, mid in rows:
        dedup[(code, name)] = (code, name, major.strip(), (mid or "").strip())
    return list(dedup.values())


def fetch_api_rows() -> tuple[list[tuple[str, str, str, str]], str]:
    """Read the live valuation payload used by StockEasy's own frontend.

    The Next.js page stopped embedding ``stockCode`` rows in September 2026,
    making the old HTML regex silently return zero records.  Fail closed on a
    coverage collapse so an empty response can never replace a good snapshot.
    """
    response = requests.get(
        API_URL,
        timeout=60,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data", {}).get("data")
    if not payload.get("success") or not isinstance(data, dict):
        raise RuntimeError("StockEasy valuation API payload shape changed")
    rows = []
    for code, item in data.items():
        if not re.fullmatch(r"[0-9A-Za-z]{6}", str(code)) or not isinstance(item, dict):
            continue
        rows.append(
            (
                str(code).upper(),
                str(item.get("name") or "").strip(),
                str(item.get("대분류") or "").strip(),
                str(item.get("중분류") or "").strip(),
            )
        )
    if len(rows) < 2000:
        raise RuntimeError(f"StockEasy coverage collapse: {len(rows)} stocks")
    raw_date = str(payload.get("data", {}).get("date") or datetime.now().strftime("%Y%m%d"))
    snapshot_date = datetime.strptime(raw_date[:8], "%Y%m%d").strftime("%Y-%m-%d")
    return rows, snapshot_date


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stockeasy_sector_membership (
            id INTEGER PRIMARY KEY,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            sector_name TEXT NOT NULL,
            sector_level TEXT NOT NULL, -- major|middle
            source_snapshot_date TEXT,
            source_url TEXT,
            observed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(stock_code, sector_name, sector_level, source_snapshot_date)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ssm_code ON stockeasy_sector_membership(stock_code)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ssm_sector ON stockeasy_sector_membership(sector_name, sector_level)"
    )


def upsert(conn: sqlite3.Connection, rows: list[tuple[str, str, str, str]], snapshot_date: str) -> dict:
    ensure_tables(conn)
    cur = conn.cursor()
    # 기존 소스 데이터 정리 후 재적재(최신 스냅샷 유지)
    cur.execute("DELETE FROM stock_sector_tags WHERE source='stockeasy_stock_analysis'")
    cur.execute("DELETE FROM stockeasy_sector_membership WHERE source_snapshot_date=?", (snapshot_date,))

    major_rows = 0
    middle_rows = 0
    for code, name, major, middle in rows:
        if major:
            cur.execute(
                """
                INSERT OR REPLACE INTO stock_sector_tags
                  (stock_code, stock_name, sector, source, confidence, is_primary, evidence, strategy, observed_at, updated_at)
                VALUES (?, ?, ?, 'stockeasy_stock_analysis', 95, 1, ?, 'major', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (code, name, major, f"{URL} major"),
            )
            cur.execute(
                """
                INSERT OR IGNORE INTO stockeasy_sector_membership
                  (stock_code, stock_name, sector_name, sector_level, source_snapshot_date, source_url)
                VALUES (?, ?, ?, 'major', ?, ?)
                """,
                (code, name, major, snapshot_date, URL),
            )
            major_rows += 1
        if middle:
            cur.execute(
                """
                INSERT OR REPLACE INTO stock_sector_tags
                  (stock_code, stock_name, sector, source, confidence, is_primary, evidence, strategy, observed_at, updated_at)
                VALUES (?, ?, ?, 'stockeasy_stock_analysis', 90, 0, ?, 'middle', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (code, name, middle, f"{URL} middle"),
            )
            cur.execute(
                """
                INSERT OR IGNORE INTO stockeasy_sector_membership
                  (stock_code, stock_name, sector_name, sector_level, source_snapshot_date, source_url)
                VALUES (?, ?, ?, 'middle', ?, ?)
                """,
                (code, name, middle, snapshot_date, URL),
            )
            middle_rows += 1

    conn.commit()
    return {"major_rows": major_rows, "middle_rows": middle_rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot-date", default=None, help="Override API snapshot date (normally unnecessary)")
    args = ap.parse_args()

    rows, api_snapshot_date = fetch_api_rows()
    snapshot_date = args.snapshot_date or api_snapshot_date

    conn = connect_primary_db(timeout=60)
    try:
        inserted = upsert(conn, rows, snapshot_date)
        major_counter = Counter(r[2] for r in rows if r[2])
        print(
            {
                "snapshot_date": snapshot_date,
                "stocks": len(rows),
                "major_sector_count": len(major_counter),
                "major_top10": major_counter.most_common(10),
                **inserted,
            }
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
