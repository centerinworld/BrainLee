#!/usr/bin/env python3
"""Backfill missing 2015-2018 Korean OHLCV from Naver Finance without overwrites."""
from __future__ import annotations

from db_compat import connect_primary_db
import argparse
import ast
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "stock.db"
ITEM_RE = re.compile(r'data="([^"]+)"')

DDL = """
CREATE TABLE IF NOT EXISTS naver_price_history_backfill (
  stock_code TEXT NOT NULL,
  date TEXT NOT NULL,
  open REAL, high REAL, low REAL, close REAL, volume REAL,
  source_url TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  PRIMARY KEY(stock_code,date)
);
CREATE INDEX IF NOT EXISTS idx_nphb_date ON naver_price_history_backfill(date,stock_code);
"""


def fetch(code: str, start: str, end: str) -> tuple[str, list[tuple], str | None]:
    # fchart silently caps requests at 3,000 sessions even for count=7000.
    # Date-bounded JSON returns the requested full history. Parse as literals,
    # never eval executable provider content.
    url = (f"https://api.finance.naver.com/siseJson.naver?symbol={code}&requestType=1"
           f"&startTime={start}&endTime={end}&timeframe=day")
    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        response.raise_for_status()
        payload = ast.literal_eval(response.text.strip())
        if not isinstance(payload, list):
            raise ValueError("Unexpected Naver response shape")
        now = datetime.now().isoformat(timespec="seconds")
        rows = []
        for f in payload[1:]:
            if not isinstance(f,list) or len(f)<6:
                raise ValueError("Malformed Naver OHLCV row")
            day=str(f[0])
            if len(day)!=8 or not day.isdigit() or not start<=day<=end:
                continue
            o,h,l,c,v=map(float,f[1:6])
            if c>0:
                iso=datetime.strptime(day,'%Y%m%d').date().isoformat()
                rows.append((code,iso,o,h,l,c,v,url,now))
        rows.sort(key=lambda r:r[1])
        if len({r[1] for r in rows}) != len(rows):
            raise ValueError("Duplicate Naver dates")
        return code, rows, None
    except Exception as exc:
        return code, [], str(exc)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="20150101")
    parser.add_argument("--end", default="20181231")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    conn = connect_primary_db(timeout=120)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=120000")
    conn.executescript(DDL)
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='price_series_registry'").fetchone():
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            """INSERT INTO price_series_registry VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(series_name) DO UPDATE SET source_detail=excluded.source_detail,
               policy_note=excluded.policy_note,updated_at=excluded.updated_at""",
            ("naver_price_history_backfill", "external_adjusted_chart_history", "2015-2018 holdout/backfill staging",
             "Naver Finance fchart daily; rows retained separately before INSERT OR IGNORE into price_history", 0,
             "Never overwrite an existing price_history row; staging table is the provenance record.", now),
        )
    codes = [r[0] for r in conn.execute(
        """SELECT DISTINCT stock_code FROM (
             SELECT stock_code FROM stock_universe
             UNION SELECT stock_code FROM price_history
             UNION SELECT stock_code FROM dart_disclosures
             UNION SELECT stock_code FROM stock_price_daily
           ) WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]' ORDER BY stock_code"""
    )]
    fetched, errors, staged = 0, 0, 0
    with ThreadPoolExecutor(max_workers=max(1,min(args.workers,8))) as pool:
        futures = [pool.submit(fetch, code, args.start, args.end) for code in codes]
        for future in as_completed(futures):
            _, rows, error = future.result()
            fetched += 1
            errors += bool(error)
            if rows:
                conn.executemany(
                    """INSERT OR IGNORE INTO naver_price_history_backfill
                       (stock_code,date,open,high,low,close,volume,source_url,fetched_at)
                       VALUES(?,?,?,?,?,?,?,?,?)""", rows
                )
                staged += len(rows)
            if fetched % 250 == 0:
                conn.commit()
                print(f"progress {fetched}/{len(codes)} staged={staged:,} errors={errors}", flush=True)
    conn.commit()
    before = conn.total_changes
    from price_integrity import ensure_schema, gate_price_batch
    ensure_schema(conn)
    rejected_codes = []
    for code in codes:
        rows = conn.execute("""SELECT date,open,high,low,close,volume
            FROM naver_price_history_backfill WHERE stock_code=?
            AND replace(date,'-','') BETWEEN ? AND ? ORDER BY date""",
            (code,args.start,args.end)).fetchall()
        if not rows:
            continue
        if not gate_price_batch(conn, code, [tuple(r) for r in rows], 'naver_historical_backfill'):
            rejected_codes.append(code)
            continue
        conn.executemany("""INSERT OR IGNORE INTO price_history
            (stock_code,date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?)""",
            [(code,*tuple(r)) for r in rows])
    print({'quarantined_codes':len(rejected_codes)}, flush=True)
    inserted = conn.total_changes - before
    conn.commit()
    coverage = [dict(zip(("year","rows","stocks"),r)) for r in conn.execute(
        """SELECT substr(date,1,4),COUNT(*),COUNT(DISTINCT stock_code) FROM price_history
           WHERE date BETWEEN '2015-01-01' AND '2018-12-31' AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
           GROUP BY 1 ORDER BY 1"""
    )]
    print({"codes":len(codes),"request_errors":errors,"staged_rows":staged,
           "inserted_missing_rows":inserted,"coverage":coverage}, flush=True)
    conn.close()


if __name__ == "__main__":
    main()
