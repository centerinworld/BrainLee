"""
Backfill missing KRX investor net-buy data from 2020 onward.

This wraps scripts/collect_krx_investor_playwright.py and only requests dates
where price_history has OHLCV rows but too few investor amount rows.

Usage:
  ./venv/bin/python scripts/backfill_krx_investor_missing_dates.py --start 2020-01-01
  ./venv/bin/python scripts/backfill_krx_investor_missing_dates.py --start 2020-01-01 --chunk-size 20
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "stock.db"
STATE_DIR = ROOT / "run" / "krx_investor_backfill"
STATE_PATH = STATE_DIR / "state.json"

sys.path.insert(0, str(ROOT))
from scripts.collect_krx_investor_playwright import collect_with_playwright  # noqa: E402


def _date_arg(v: str) -> str:
    if len(v) == 8 and v.isdigit():
        return f"{v[:4]}-{v[4:6]}-{v[6:]}"
    date.fromisoformat(v)
    return v


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {"done": [], "failed": []}
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {"done": [], "failed": []}


def _save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    tmp.replace(STATE_PATH)


def missing_dates(start: str, end: str, min_rows: int) -> list[str]:
    conn = sqlite3.connect(DB_PATH, timeout=60)
    try:
        rows = conn.execute(
            """
            WITH d AS (
                SELECT
                    substr(date, 1, 10) AS dt,
                    COUNT(*) AS price_rows,
                    SUM(CASE WHEN COALESCE(inst_net_buy_amt, 0) != 0 THEN 1 ELSE 0 END) AS inst_rows,
                    SUM(CASE WHEN COALESCE(frn_net_buy_amt, 0) != 0 THEN 1 ELSE 0 END) AS frn_rows
                FROM price_history
                WHERE substr(date, 1, 10) BETWEEN ? AND ?
                  AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
                  AND close > 0
                GROUP BY substr(date, 1, 10)
            )
            SELECT dt
            FROM d
            WHERE price_rows >= ?
              AND inst_rows < ?
              AND frn_rows < ?
            ORDER BY dt
            """,
            (start, end, min_rows, min_rows, min_rows),
        ).fetchall()
        return [r[0].replace("-", "") for r in rows]
    finally:
        conn.close()


def chunks(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2020-01-01", type=_date_arg)
    ap.add_argument("--end", default=date.today().isoformat(), type=_date_arg)
    ap.add_argument("--min-rows", type=int, default=50, help="date is covered when both inst/frn amount rows >= this")
    ap.add_argument("--chunk-size", type=int, default=10)
    ap.add_argument("--sleep", type=float, default=5.0)
    ap.add_argument("--show-browser", action="store_true")
    ap.add_argument("--retry-failed", action="store_true")
    args = ap.parse_args()

    state = _load_state()
    done = set(state.get("done", []))
    failed = set(state.get("failed", []))

    dates = missing_dates(args.start, args.end, args.min_rows)
    if not args.retry_failed:
        dates = [d for d in dates if d not in done and d not in failed]
    else:
        dates = [d for d in dates if d not in done]

    print(f"[KRX backfill] missing dates: {len(dates)} ({args.start} ~ {args.end})")
    if not dates:
        return 0

    for batch in chunks(dates, max(1, args.chunk_size)):
        print(f"[KRX backfill] collecting {batch[0]} ~ {batch[-1]} ({len(batch)} days)")
        stats = collect_with_playwright(
            dates=batch,
            dry_run=False,
            headless=not args.show_browser,
        )
        print(f"[KRX backfill] stats={stats}")

        if stats.get("success", 0) == 0 and stats.get("records", 0) == 0:
            for d in batch:
                failed.add(d)
            state["failed"] = sorted(failed)
            _save_state(state)
            print("[KRX backfill] no successful records; stopping to avoid account/API retry loops")
            return 2

        for d in batch:
            done.add(d)
            failed.discard(d)
        state["done"] = sorted(done)
        state["failed"] = sorted(failed)
        state["last_run_at"] = datetime.now().isoformat(timespec="seconds")
        _save_state(state)
        time.sleep(args.sleep)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
