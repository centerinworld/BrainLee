#!/usr/bin/env python3
"""Backfill adjusted OHLCV for delisted US tickers through Tiingo.

Tiingo is used only because free Yahoo/KIS/Nasdaq endpoints drop delisted
symbols.  The script is fail-closed, defaults to dry-run, never overwrites an
existing price row, and records every applied batch in ``data_fix_log``.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import uuid

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from db_compat import connect_primary_db


DEFAULT_TICKERS = """ABMD ANSS ATVI CERN CMA CTLT CTRA CTXS DAY DFS DISCA DISCK DISH
DRE FBHS FRC GPS HES HOLX INFO IPG JNPR K MRO NLSN PBCT PEAK PXD SATS SBNY SEE SIVB
TWTR VLTO WBA WRK XLNX""".split()


def normalize_rows(ticker: str, payload: object) -> list[tuple]:
    if not isinstance(payload, list):
        raise ValueError(f"{ticker}: Tiingo response is not a row list")
    rows: list[tuple] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        day = str(item.get("date") or "")[:10]
        values = [item.get(k) for k in ("adjOpen", "adjHigh", "adjLow", "adjClose", "adjVolume")]
        if not day or any(v is None for v in values):
            continue
        opn, high, low, close, volume = map(float, values)
        if min(opn, high, low, close) <= 0 or high < max(opn, close) or low > min(opn, close):
            continue
        rows.append((ticker.upper(), day, close, volume, opn, high, low))
    if not rows:
        raise ValueError(f"{ticker}: no valid adjusted OHLCV rows")
    return rows


def fetch_ticker(ticker: str, start: str, end: str, token: str) -> list[tuple]:
    response = requests.get(
        f"https://api.tiingo.com/tiingo/daily/{ticker}/prices",
        params={"startDate": start, "endDate": end, "resampleFreq": "daily", "token": token},
        headers={"Content-Type": "application/json", "User-Agent": "stock-dashboard/1.0"},
        timeout=60,
    )
    if response.status_code != 200:
        raise RuntimeError(f"{ticker}: Tiingo HTTP {response.status_code}: {response.text[:200]}")
    return normalize_rows(ticker, response.json())


def apply_rows(rows_by_ticker: dict[str, list[tuple]]) -> dict:
    run_id = f"us_delisted_tiingo_{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc).isoformat()
    conn = connect_primary_db(timeout=120)
    inserted = 0
    try:
        before = conn.execute("SELECT COUNT(*) FROM us_price_history").fetchone()[0]
        for ticker, rows in rows_by_ticker.items():
            conn.executemany("""INSERT INTO us_price_history
                (ticker,date,close,volume,created_at,open,high,low)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(ticker,date) DO NOTHING""", [
                (t, d, close, volume, now, opn, high, low)
                for t, d, close, volume, opn, high, low in rows
            ])
        after = conn.execute("SELECT COUNT(*) FROM us_price_history").fetchone()[0]
        inserted = int(after - before)
        conn.execute("""INSERT INTO data_fix_log
            (fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,
             new_value_summary,source,run_id) VALUES(?,?,?,?,?,?,?,?,?)""",
            (now, "us_price_history", ",".join(sorted(rows_by_ticker)), inserted,
             "insert_missing_adjusted_ohlcv_only", "existing rows preserved",
             f"Tiingo adjusted OHLCV; requested={sum(map(len, rows_by_ticker.values()))}",
             "tiingo_daily_api", run_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    verify = connect_primary_db(readonly=True, timeout=120)
    try:
        logged = verify.execute("SELECT row_count FROM data_fix_log WHERE run_id=?", (run_id,)).fetchone()
    finally:
        verify.close()
    if not logged or int(logged[0]) != inserted:
        raise RuntimeError("post-write audit verification failed")
    return {"run_id": run_id, "inserted": inserted}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", default=",".join(DEFAULT_TICKERS))
    parser.add_argument("--start", default="2021-05-24")
    parser.add_argument("--end", default="2026-09-25")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    token = os.getenv("TIINGO_API_KEY", "").strip()
    if not token:
        raise SystemExit("TIINGO_API_KEY is required; no database changes made")
    tickers = sorted({x.strip().upper() for x in args.tickers.split(",") if x.strip()})
    rows_by_ticker: dict[str, list[tuple]] = {}
    failures: dict[str, str] = {}
    for ticker in tickers:
        try:
            rows_by_ticker[ticker] = fetch_ticker(ticker, args.start, args.end, token)
        except Exception as exc:
            failures[ticker] = str(exc)
    summary = {"tickers_ok": len(rows_by_ticker), "rows": sum(map(len, rows_by_ticker.values())),
               "failures": failures, "apply": args.apply}
    if args.apply and rows_by_ticker:
        summary.update(apply_rows(rows_by_ticker))
    print(summary)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
