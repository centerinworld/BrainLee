#!/usr/bin/env python3
"""Rebuild US moving averages and 52-week high/low from stored adjusted OHLC.

Dry-run is the default.  ``--apply`` backs up the previous factor values,
updates them in one transaction and verifies the committed values through an
independent read connection.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db
from us_market_data import technical_snapshot


FIELDS = ("ma5", "ma20", "ma50", "ma60", "ma200", "high_52w", "low_52w")


def load_values(conn) -> dict[str, tuple[float | None, ...]]:
    rows = conn.execute(
        """SELECT ticker,open,high,low,close FROM (
             SELECT ticker,open,high,low,close,date,
                    ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) rn
               FROM us_price_history
              WHERE open>0 AND high>0 AND low>0 AND close>0
           ) x WHERE rn<=252 ORDER BY ticker,rn DESC"""
    ).fetchall()
    grouped: dict[str, list[tuple[float, float, float, float]]] = {}
    for ticker, opn, high, low, close in rows:
        grouped.setdefault(str(ticker), []).append(
            (float(opn), float(high), float(low), float(close))
        )
    out = {}
    for ticker, bars in grouped.items():
        values = technical_snapshot(bars)
        out[ticker] = tuple(getattr(values, field) for field in FIELDS)
    return out


def ensure_schema(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS us_factor_technical_backup (
        batch_id TEXT NOT NULL,ticker TEXT NOT NULL,ma5 REAL,ma20 REAL,ma50 REAL,
        ma60 REAL,ma200 REAL,high_52w REAL,low_52w REAL,backed_up_at TEXT NOT NULL,
        PRIMARY KEY(batch_id,ticker))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS us_factor_technical_runs (
        batch_id TEXT PRIMARY KEY,status TEXT NOT NULL,target_count INTEGER NOT NULL,
        updated_count INTEGER NOT NULL,postcheck_status TEXT NOT NULL,created_at TEXT NOT NULL)""")


def apply(values: dict[str, tuple[float | None, ...]]) -> dict:
    batch_id = f"usfactor_{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc).isoformat()
    conn = connect_primary_db(timeout=300)
    try:
        ensure_schema(conn)
        tickers = sorted(values)
        existing = conn.execute(
            f"SELECT ticker,{','.join(FIELDS)} FROM us_factor_snapshot WHERE ticker=ANY(?)",
            (tickers,),
        ).fetchall()
        conn.executemany(
            """INSERT INTO us_factor_technical_backup
            (batch_id,ticker,ma5,ma20,ma50,ma60,ma200,high_52w,low_52w,backed_up_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            [(batch_id, *tuple(row), now) for row in existing],
        )
        conn.executemany(
            """UPDATE us_factor_snapshot SET
               ma5=?,ma20=?,ma50=?,ma60=?,ma200=?,high_52w=?,low_52w=?,
               above_200ma=CASE
                 WHEN price IS NOT NULL
                  AND CAST(? AS DOUBLE PRECISION) IS NOT NULL
                  AND price>CAST(? AS DOUBLE PRECISION) THEN 1 ELSE 0 END,
               updated_at=CURRENT_TIMESTAMP WHERE ticker=?""",
            [(*row, row[4], row[4], ticker) for ticker, row in values.items()],
        )
        conn.execute(
            """INSERT INTO us_factor_technical_runs
            (batch_id,status,target_count,updated_count,postcheck_status,created_at)
            VALUES(?,'success',?,?,'pending',?)""",
            (batch_id, len(values), len(existing), now),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    verify = connect_primary_db(readonly=True, timeout=300)
    mismatches = 0
    try:
        for ticker, expected in values.items():
            row = verify.execute(
                f"SELECT {','.join(FIELDS)} FROM us_factor_snapshot WHERE ticker=?", (ticker,)
            ).fetchone()
            if row is None:
                continue
            for actual, wanted in zip(tuple(row), expected):
                if wanted is None:
                    mismatches += int(actual is not None)
                elif actual is None or not math.isclose(float(actual), wanted, rel_tol=1e-12):
                    mismatches += 1
    finally:
        verify.close()
    status = "passed" if mismatches == 0 else "failed"
    update = connect_primary_db(timeout=120)
    try:
        update.execute(
            "UPDATE us_factor_technical_runs SET postcheck_status=? WHERE batch_id=?",
            (status, batch_id),
        )
        update.commit()
    finally:
        update.close()
    if mismatches:
        raise RuntimeError(f"technical-factor postcheck failed: {mismatches} mismatches")
    return {"batch_id": batch_id, "target_count": len(values),
            "updated_count": len(existing), "postcheck_status": status}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    conn = connect_primary_db(readonly=True, timeout=300)
    try:
        values = load_values(conn)
    finally:
        conn.close()
    summary = {
        "apply": args.apply,
        "target_count": len(values),
        "ma50_available": sum(v[2] is not None for v in values.values()),
        "ma200_available": sum(v[4] is not None for v in values.values()),
    }
    if args.apply:
        summary = apply(values)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
