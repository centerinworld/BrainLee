#!/usr/bin/env python3
"""Audit and repair a US ticker whose adjusted-price basis was spliced.

Dry-run is the default.  ``--apply`` backs up every existing row, replaces the
entire ticker history from one current Yahoo auto-adjusted snapshot, records a
source hash and independently verifies the committed series.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db
from us_price_integrity import USPricePoint, basis_whiplashes, overlap_basis_mismatches


def download_rows(ticker: str) -> list[tuple]:
    import yfinance as yf

    frame = yf.download(ticker, period="max", auto_adjust=True, actions=False,
                        progress=False, threads=False)
    if frame.empty:
        raise RuntimeError(f"Yahoo returned no history for {ticker}")
    if getattr(frame.columns, "nlevels", 1) > 1:
        if ticker in frame.columns.get_level_values(-1):
            frame = frame.xs(ticker, axis=1, level=-1)
        elif ticker in frame.columns.get_level_values(0):
            frame = frame[ticker]
    rows = []
    for index, row in frame.dropna(subset=["Close"]).iterrows():
        values = [float(row[name]) for name in ("Open", "High", "Low", "Close")]
        tolerance = max(values) * 1e-6
        if (min(values) <= 0
                or values[1] + tolerance < max(values[0], values[3])
                or values[2] - tolerance > min(values[0], values[3])):
            raise RuntimeError(f"invalid Yahoo OHLC for {ticker} {str(index)[:10]}")
        volume = float(row.get("Volume") or 0)
        rows.append((ticker, str(index)[:10], *values, volume))
    if len(rows) < 250:
        raise RuntimeError(f"Yahoo history too short for {ticker}: {len(rows)}")
    return rows


def points(rows: list[tuple]) -> list[USPricePoint]:
    return [USPricePoint(row[1], float(row[5])) for row in rows]


def run(ticker: str, *, apply: bool = False) -> dict:
    ticker = ticker.strip().upper()
    incoming = download_rows(ticker)
    incoming_whips = basis_whiplashes(points(incoming))
    conn = connect_primary_db(readonly=not apply, timeout=120)
    try:
        old = [tuple(row) for row in conn.execute(
            """SELECT ticker,date,open,high,low,close,volume,created_at
                 FROM us_price_history WHERE ticker=? ORDER BY date""", (ticker,)
        ).fetchall()]
        old_whips = basis_whiplashes([
            USPricePoint(str(row[1])[:10], float(row[5])) for row in old if row[5]
        ])
        mismatch = overlap_basis_mismatches(
            [(row[1], row[5]) for row in old], [(row[1], row[5]) for row in incoming],
        )
        payload = [(row[1],) + tuple(round(float(x), 8) for x in row[2:]) for row in incoming]
        source_hash = hashlib.sha256(
            json.dumps(payload, separators=(",", ":")).encode()
        ).hexdigest()
        summary = {
            "ticker": ticker, "apply": apply, "old_rows": len(old),
            "incoming_rows": len(incoming), "old_whiplashes": old_whips,
            "incoming_whiplashes": incoming_whips,
            "same_date_mismatch_count": len(mismatch),
            "mismatch_sample": mismatch[:10], "source_hash": source_hash,
        }
        if not apply:
            return summary

        batch_id = f"uspx_{uuid.uuid4().hex}"
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""CREATE TABLE IF NOT EXISTS us_price_history_repair_backup (
            batch_id TEXT NOT NULL, ticker TEXT NOT NULL, date TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            original_created_at TEXT, backed_up_at TEXT NOT NULL, reason TEXT NOT NULL,
            PRIMARY KEY(batch_id,ticker,date))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS us_price_repair_runs (
            batch_id TEXT PRIMARY KEY, ticker TEXT NOT NULL, applied_at TEXT NOT NULL,
            source TEXT NOT NULL, source_hash TEXT NOT NULL, old_rows INTEGER NOT NULL,
            new_rows INTEGER NOT NULL, mismatch_count INTEGER NOT NULL,
            old_whiplash_count INTEGER NOT NULL, postcheck_status TEXT NOT NULL,
            reason TEXT NOT NULL)""")
        conn.executemany("""INSERT INTO us_price_history_repair_backup
            (batch_id,ticker,date,open,high,low,close,volume,original_created_at,
             backed_up_at,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)""", [
            (batch_id, *row, now, "adjusted_price_basis_splice") for row in old
        ])
        conn.execute("DELETE FROM us_price_history WHERE ticker=?", (ticker,))
        conn.executemany("""INSERT INTO us_price_history
            (ticker,date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?)""", incoming)
        conn.execute("""INSERT INTO us_price_repair_runs
            (batch_id,ticker,applied_at,source,source_hash,old_rows,new_rows,mismatch_count,
             old_whiplash_count,postcheck_status,reason)
            VALUES(?,?,?,?,?,?,?,?,?,'pending',?)""",
            (batch_id, ticker, now, "Yahoo Finance auto_adjust=True period=max",
             source_hash, len(old), len(incoming), len(mismatch), len(old_whips),
             "Replace mixed adjusted-price basis with one full-history snapshot"))
        conn.commit()
        summary["batch_id"] = batch_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    verify = connect_primary_db(timeout=120)
    try:
        saved = [tuple(row) for row in verify.execute(
            "SELECT ticker,date,open,high,low,close,volume FROM us_price_history WHERE ticker=? ORDER BY date",
            (ticker,),
        ).fetchall()]
        post_whips = basis_whiplashes(points(saved))
        exact = len(saved) == len(incoming) and all(
            a[0:2] == b[0:2] and all(abs(float(x) - float(y)) < 1e-7 for x, y in zip(a[2:], b[2:]))
            for a, b in zip(saved, incoming)
        )
        status = "passed" if exact else "failed"
        verify.execute("UPDATE us_price_repair_runs SET postcheck_status=? WHERE batch_id=?",
                       (status, summary["batch_id"]))
        verify.commit()
        summary.update({"postcheck_status": status, "post_whiplashes": post_whips,
                        "verified_rows": len(saved)})
        if status != "passed":
            raise RuntimeError(f"post-write verification failed: {summary}")
        return summary
    finally:
        verify.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ticker", nargs="+")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    results = [run(ticker, apply=args.apply) for ticker in args.ticker]
    print(json.dumps(results[0] if len(results) == 1 else results,
                     indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
