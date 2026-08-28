#!/usr/bin/env python3
"""
Refresh recent DART disclosure list in one date-range pass.

The legacy collector queries per stock and skips stocks fetched within seven
days, which can miss same-week new filings.  This script queries OpenDART's
date-range list endpoint directly and upserts fresh disclosure rows without
deleting each stock's older history.
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests


ROOT = Path("/Applications/stock_dashboard")
DB_PATH = ROOT / "stock.db"
API_URL = "https://opendart.fss.or.kr/api/list.json"

import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DDL = """CREATE TABLE IF NOT EXISTS dart_disclosures (
    stock_code TEXT,
    rcept_no   TEXT,
    rcept_dt   TEXT,
    report_nm  TEXT,
    flr_nm     TEXT,
    corp_name  TEXT,
    dart_url   TEXT,
    fetched_at TEXT,
    PRIMARY KEY (stock_code, rcept_no)
)"""


def load_keys() -> list[str]:
    import config

    keys = [
        getattr(config, "DART_API_KEY2", None),
        getattr(config, "DART_API_KEY", None),
        getattr(config, "DART_API_KEY3", None),
    ]
    return [k for k in keys if k]


def normalize_date(s: str) -> str:
    s = str(s or "")
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def request_page(keys: list[str], params: dict, key_idx: int) -> tuple[dict, int]:
    last_error = None
    for offset in range(len(keys)):
        idx = (key_idx + offset) % len(keys)
        p = dict(params)
        p["crtfc_key"] = keys[idx]
        resp = requests.get(API_URL, params=p, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        status = str(data.get("status", ""))
        if status == "000":
            return data, idx
        last_error = f"{status} {data.get('message')}"
        if status not in {"020", "800"}:
            break
    raise RuntimeError(f"OpenDART list failed: {last_error}")


def upsert_rows(conn: sqlite3.Connection, items: list[dict]) -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for item in items:
        stock_code = str(item.get("stock_code") or "").zfill(6)
        rcept_no = str(item.get("rcept_no") or "")
        if not (stock_code.isdigit() and len(stock_code) == 6 and rcept_no):
            continue
        rows.append(
            (
                stock_code,
                rcept_no,
                normalize_date(item.get("rcept_dt")),
                str(item.get("report_nm") or ""),
                str(item.get("flr_nm") or ""),
                str(item.get("corp_name") or ""),
                f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
                now,
            )
        )
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO dart_disclosures
          (stock_code, rcept_no, rcept_dt, report_nm, flr_nm, corp_name, dart_url, fetched_at)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(stock_code, rcept_no) DO UPDATE SET
          rcept_dt=excluded.rcept_dt,
          report_nm=excluded.report_nm,
          flr_nm=excluded.flr_nm,
          corp_name=excluded.corp_name,
          dart_url=excluded.dart_url,
          fetched_at=excluded.fetched_at
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh recent DART disclosures by date range")
    parser.add_argument("--days", type=int, default=5, help="lookback days including today")
    parser.add_argument("--start", help="YYYYMMDD start date")
    parser.add_argument("--end", help="YYYYMMDD end date")
    parser.add_argument("--page-count", type=int, default=100, help="OpenDART page_count, max 100")
    parser.add_argument("--sleep", type=float, default=0.15, help="seconds between pages")
    args = parser.parse_args()

    end = datetime.strptime(args.end, "%Y%m%d").date() if args.end else date.today()
    start = datetime.strptime(args.start, "%Y%m%d").date() if args.start else end - timedelta(days=max(args.days - 1, 0))
    keys = load_keys()
    if not keys:
        raise SystemExit("No DART API key configured")

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute(DDL)
    conn.commit()

    key_idx = 0
    page_no = 1
    inserted = 0
    total_count = None
    while True:
        params = {
            "bgn_de": start.strftime("%Y%m%d"),
            "end_de": end.strftime("%Y%m%d"),
            "page_no": page_no,
            "page_count": min(max(args.page_count, 1), 100),
        }
        data, key_idx = request_page(keys, params, key_idx)
        items = data.get("list") or []
        total_count = int(data.get("total_count") or len(items))
        inserted += upsert_rows(conn, items)
        if page_no * params["page_count"] >= total_count or not items:
            break
        page_no += 1
        time.sleep(args.sleep)

    conn.close()
    print(
        {
            "start": start.strftime("%Y-%m-%d"),
            "end": end.strftime("%Y-%m-%d"),
            "total_count": total_count,
            "upserted": inserted,
            "pages": page_no,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
