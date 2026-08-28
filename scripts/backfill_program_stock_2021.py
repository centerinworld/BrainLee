#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.collect_broker_program_trading import (  # noqa: E402
    DB_PATH,
    ensure_tables,
    iter_weekdays,
    load_stock_codes,
    upsert_stock,
)
from collectors.kiwoom_collector import KiwoomCollector  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / "run" / "program_stock_backfill_2021"
STATE_PATH = RUN_DIR / "state.json"


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH), timeout=300)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=300000")
    return con


def anchor_dates(start: str, end: str, stride: int) -> list[str]:
    days = iter_weekdays(start, end)
    if not days:
        return []
    anchors = days[::stride]
    if anchors[-1] != days[-1]:
        anchors.append(days[-1])
    return anchors


def existing_count(con: sqlite3.Connection, stock_code: str, start: str, end: str) -> int:
    row = con.execute(
        """
        SELECT COUNT(*)
        FROM broker_program_stock_daily
        WHERE source = 'kiwoom'
          AND stock_code = ?
          AND replace(substr(dt,1,10), '-', '') BETWEEN ? AND ?
        """,
        (stock_code, start, end),
    ).fetchone()
    return int(row[0] or 0)


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def collect_kiwoom_stock_only(
    con: sqlite3.Connection,
    kw: KiwoomCollector,
    anchor: str,
    stock_code: str,
) -> int:
    response = requests.post(
        f"{kw.base_url}/api/dostk/mrkcond",
        headers=kw._auth_headers("ka90013"),
        json={"amt_qty_tp": "1", "stk_cd": stock_code, "date": anchor},
        timeout=15,
    )
    data = response.json()
    rows = data.get("stk_daly_prm_trde_trnsn") if data.get("return_code") == 0 else None
    if not rows:
        return 0
    saved = 0
    for row in rows:
        row_dt = row.get("dt") or anchor
        upsert_stock(con, "kiwoom", stock_code, row_dt, row)
        saved += 1
    return saved


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill Kiwoom stock-level program trading rows from 2021, excluding ETF/ETN."
    )
    parser.add_argument("--start", default="20210101")
    parser.add_argument("--end", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--stride", type=int, default=20, help="Business-day anchor stride.")
    parser.add_argument("--limit-stocks", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=0.05)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--min-existing-rows", type=int, default=900)
    args = parser.parse_args()

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    anchors = anchor_dates(args.start, args.end, args.stride)
    if not anchors:
        raise SystemExit("No weekday anchors in requested range.")

    state = load_state() if args.resume else {}
    completed = set(state.get("completed", []))
    state.update(
        {
            "start": args.start,
            "end": args.end,
            "stride": args.stride,
            "anchor_count": len(anchors),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    state.setdefault("completed", sorted(completed))
    state.setdefault("errors", [])

    with connect() as con:
        ensure_tables(con)
        stocks = load_stock_codes(con, args.limit_stocks or None)
        state["stock_count"] = len(stocks)
        save_state(state)

        kw = KiwoomCollector()
        if not kw.ensure_token():
            raise RuntimeError("Kiwoom token issue failed")

        for idx, stock_code in enumerate(stocks, 1):
            if stock_code in completed:
                continue
            before = existing_count(con, stock_code, args.start, args.end)
            if before >= args.min_existing_rows:
                completed.add(stock_code)
                state["completed"] = sorted(completed)
                state["last_stock"] = stock_code
                state["updated_at"] = datetime.now().isoformat(timespec="seconds")
                save_state(state)
                continue

            print(f"[{idx}/{len(stocks)}] {stock_code} anchors={len(anchors)} existing={before}", flush=True)
            stock_rows = 0
            try:
                for anchor in anchors:
                    stock_rows += collect_kiwoom_stock_only(con, kw, anchor, stock_code)
                    con.commit()
                    time.sleep(args.sleep)
                after = existing_count(con, stock_code, args.start, args.end)
                completed.add(stock_code)
                state["completed"] = sorted(completed)
                state["last_stock"] = stock_code
                state["last_stock_rows_before"] = before
                state["last_stock_rows_after"] = after
                state["last_stock_rows_saved_this_run"] = stock_rows
                state["updated_at"] = datetime.now().isoformat(timespec="seconds")
                save_state(state)
                print(f"  done {stock_code}: saved={stock_rows} range_rows={after}", flush=True)
            except Exception as exc:
                con.rollback()
                state["errors"].append(
                    {
                        "stock_code": stock_code,
                        "error": f"{type(exc).__name__}: {exc}",
                        "at": datetime.now().isoformat(timespec="seconds"),
                    }
                )
                state["updated_at"] = datetime.now().isoformat(timespec="seconds")
                save_state(state)
                print(f"  ERROR {stock_code}: {type(exc).__name__}: {exc}", flush=True)
                time.sleep(max(args.sleep, 1.0))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
