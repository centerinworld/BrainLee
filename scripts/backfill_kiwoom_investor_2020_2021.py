#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.kiwoom_collector import KiwoomCollector
from db_utils import STOCK_DB_PATH


RUN_DIR = ROOT / "run" / "kiwoom_investor_2020_2021"
PROGRESS = RUN_DIR / "progress.json"
SUMMARY = RUN_DIR / "summary.json"


def now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def load_codes(limit: int | None = None, explicit_codes: list[str] | None = None) -> list[str]:
    if explicit_codes:
        return [c.strip().zfill(6) for c in explicit_codes if c.strip()]
    conn = sqlite3.connect(str(STOCK_DB_PATH), timeout=30)
    try:
        sql = """
            SELECT stock_code
            FROM stock_universe
            WHERE market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
              AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
              AND COALESCE(stock_name, '') NOT LIKE '%스팩%'
              AND COALESCE(stock_name, '') NOT LIKE '%SPAC%'
            ORDER BY COALESCE(market_cap, 0) DESC, stock_code
        """
        params: tuple = ()
        if limit:
            sql += " LIMIT ?"
            params = (limit,)
        rows = conn.execute(sql, params).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def load_done() -> set[str]:
    if not PROGRESS.exists():
        return set()
    try:
        data = json.loads(PROGRESS.read_text(encoding="utf-8"))
        return set(data.get("done_codes") or [])
    except Exception:
        return set()


def save_progress(done_codes: set[str], summary: dict) -> None:
    payload = {
        "updated_at": now(),
        "done_codes": sorted(done_codes),
        "summary": summary,
    }
    PROGRESS.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def count_window() -> dict:
    conn = sqlite3.connect(str(STOCK_DB_PATH), timeout=30)
    try:
        row = conn.execute(
            """
            SELECT
              COUNT(*) AS rows,
              COUNT(DISTINCT stock_code) AS stocks,
              MIN(bas_dt) AS min_dt,
              MAX(bas_dt) AS max_dt
            FROM investor_trading_daily
            WHERE bas_dt >= '2020-01-01' AND bas_dt <= '2021-12-31'
            """
        ).fetchone()
        by_year = conn.execute(
            """
            SELECT substr(bas_dt, 1, 4) AS year, COUNT(*) AS rows, COUNT(DISTINCT stock_code) AS stocks
            FROM investor_trading_daily
            WHERE bas_dt >= '2020-01-01' AND bas_dt <= '2021-12-31'
            GROUP BY year
            ORDER BY year
            """
        ).fetchall()
        return {
            "rows": row[0],
            "stocks": row[1],
            "min_dt": row[2],
            "max_dt": row[3],
            "by_year": [{"year": r[0], "rows": r[1], "stocks": r[2]} for r in by_year],
        }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dt", default="20211231", help="Kiwoom ka10059 기준일 YYYYMMDD")
    parser.add_argument("--max-pages", type=int, default=6, help="1페이지 100행. 6페이지면 대략 2020~2021 커버")
    parser.add_argument("--limit-stocks", type=int, default=0)
    parser.add_argument("--codes", default="", help="comma-separated stock codes for a targeted run")
    parser.add_argument("--sleep", type=float, default=0.35)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    explicit_codes = [c for c in args.codes.split(",") if c.strip()]
    codes = load_codes(args.limit_stocks or None, explicit_codes=explicit_codes)
    done_codes = set() if args.no_resume else load_done()
    todo = [c for c in codes if c not in done_codes]

    summary = {
        "base_dt": args.base_dt,
        "max_pages": args.max_pages,
        "total_codes": len(codes),
        "todo_codes": len(todo),
        "done_at_start": len(done_codes),
        "ok": 0,
        "failed": 0,
        "saved_rows": 0,
        "sample_errors": [],
    }

    log(f"START kiwoom investor 2020-2021 base_dt={args.base_dt} pages={args.max_pages} codes={len(codes)} todo={len(todo)}")
    kc = KiwoomCollector()
    health = kc.health_check()
    summary["health"] = health
    if not health.get("ok"):
        SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"STOP kiwoom_not_ready {health}")
        return 1

    for i, code in enumerate(todo, 1):
        result = kc.fetch_investor_by_stock(code, base_dt=args.base_dt, max_pages=args.max_pages)
        if result.get("ok"):
            summary["ok"] += 1
            summary["saved_rows"] += int(result.get("saved", 0) or 0)
            done_codes.add(code)
        else:
            summary["failed"] += 1
            if len(summary["sample_errors"]) < 30:
                summary["sample_errors"].append({"stock_code": code, "result": result})

        if i == 1 or i % 25 == 0 or i == len(todo):
            summary["window"] = count_window()
            save_progress(done_codes, summary)
            log(
                f"progress {i}/{len(todo)} ok={summary['ok']} failed={summary['failed']} "
                f"saved={summary['saved_rows']} window={summary['window']}"
            )
        time.sleep(args.sleep)

    summary["window"] = count_window()
    summary["finished_at"] = now()
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    save_progress(done_codes, summary)
    log(f"DONE {summary}")
    return 0 if summary["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
