#!/usr/bin/env python3
"""Bulk repair of the 2026-03-31~04-07 batch-interpolation incident.

Found 2026-09-23 (hermes.md "최우선 발견: price_history 전체 29%..."): 2,969,580
rows across 2,662 stocks have a non-integer `close` (Korean market closes are
always whole won - a non-integer value can never be a real print). created_at
for 97.6% of them clusters on 2026-03-31/04-05/04-07, so this is one dated
batch incident, not gradual drift. Spot check (175330/JB금융지주, Jan-Feb 2022)
confirmed the pattern directly: on any given date either the row is a real,
correct integer (matching pykrx exactly) or a bogus interpolated decimal -
never both partially - so the fix is a straight per-row replacement wherever
close is non-integer, using marcap (KRX-official-sourced, unadjusted) as the
independent source, one year at a time.

Per year: stage that year's marcap file into a Postgres temp table, then do a
single backup-insert + single update join against `price_history` restricted
to non-integer-close rows for that year - both server-side, not per-row
Python, since this is a multi-million-row operation.

Usage: --year YYYY (one year per invocation, run 2010..2026 in sequence),
--apply to actually write (default dry-run just reports candidate count).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402
from price_integrity import native_script  # noqa: E402
from marcap_client import ensure_year  # noqa: E402

BACKUP_DDL = """
CREATE TABLE IF NOT EXISTS price_history_fix_backup (
  run_id TEXT NOT NULL, stock_code TEXT NOT NULL, date TEXT NOT NULL,
  old_open DOUBLE PRECISION, old_high DOUBLE PRECISION, old_low DOUBLE PRECISION,
  old_close DOUBLE PRECISION, old_volume DOUBLE PRECISION,
  new_open DOUBLE PRECISION, new_high DOUBLE PRECISION, new_low DOUBLE PRECISION,
  new_close DOUBLE PRECISION, new_volume DOUBLE PRECISION,
  reason TEXT NOT NULL, fixed_at TEXT NOT NULL,
  PRIMARY KEY(run_id, stock_code, date)
)
"""

REASON = (
    "bulk batch-interpolation repair (2026-03-31~04-07 incident, see hermes.md "
    "'최우선 발견'): close was non-integer (never a real KRX print) - replaced "
    "with marcap raw OHLCV for the same (stock_code, date)."
)


def stage_marcap_year(conn, year: int) -> str:
    path = ensure_year(year)
    df = pd.read_parquet(path)
    df["Date"] = df["Date"].astype(str)
    df = df[["Code", "Date", "Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Code", "Date"])

    conn.execute("DROP TABLE IF EXISTS marcap_staging")
    conn.execute(
        """CREATE TEMP TABLE marcap_staging (
             stock_code TEXT, date TEXT, open DOUBLE PRECISION, high DOUBLE PRECISION,
             low DOUBLE PRECISION, close DOUBLE PRECISION, volume DOUBLE PRECISION
           )"""
    )
    raw = getattr(conn, "_connection", None) or conn
    cur = raw.cursor() if hasattr(raw, "cursor") else conn.cursor()
    rows = list(df.itertuples(index=False, name=None))
    with cur.copy(
        "COPY marcap_staging (stock_code,date,open,high,low,close,volume) FROM STDIN"
    ) as copy:
        for r in rows:
            copy.write_row(r)
    conn.execute("CREATE INDEX ON marcap_staging (stock_code, date)")
    return f"staged {len(rows)} marcap rows for {year}"


def run(year: int, dry_run: bool) -> dict:
    conn = connect_primary_db(timeout=300)
    try:
        native_script(conn, BACKUP_DDL)
        stage_msg = stage_marcap_year(conn, year)

        candidates = conn.execute(
            """SELECT count(*) FROM price_history ph
               JOIN marcap_staging m ON ph.stock_code=m.stock_code AND ph.date=m.date
               WHERE ph.close>0 AND ph.close != FLOOR(ph.close)
                 AND ph.date>=? AND ph.date<?""",
            (f"{year}-01-01", f"{year+1}-01-01"),
        ).fetchone()[0]

        result = {"year": year, "stage": stage_msg, "candidates": candidates, "dry_run": dry_run}
        if dry_run:
            return result

        run_id = f"bulk_marcap_interpolation_repair_{year}_{datetime.now().strftime('%H%M%S')}"
        now = datetime.now().isoformat(timespec="seconds")

        inserted = conn.execute(
            """INSERT INTO price_history_fix_backup
                 (run_id,stock_code,date,old_open,old_high,old_low,old_close,old_volume,
                  new_open,new_high,new_low,new_close,new_volume,reason,fixed_at)
               SELECT ?, ph.stock_code, ph.date, ph.open, ph.high, ph.low, ph.close, ph.volume,
                      m.open, m.high, m.low, m.close, m.volume, ?, ?
               FROM price_history ph
               JOIN marcap_staging m ON ph.stock_code=m.stock_code AND ph.date=m.date
               WHERE ph.close>0 AND ph.close != FLOOR(ph.close)
                 AND ph.date>=? AND ph.date<?
               ON CONFLICT (run_id, stock_code, date) DO NOTHING""",
            (run_id, REASON, now, f"{year}-01-01", f"{year+1}-01-01"),
        )

        conn.execute("SELECT set_config('app.price_basis_checked','1',true)")
        updated = conn.execute(
            """UPDATE price_history ph SET
                 open=m.open, high=m.high, low=m.low, close=m.close, volume=m.volume
               FROM marcap_staging m
               WHERE ph.stock_code=m.stock_code AND ph.date=m.date
                 AND ph.close>0 AND ph.close != FLOOR(ph.close)
                 AND ph.date>=? AND ph.date<?""",
            (f"{year}-01-01", f"{year+1}-01-01"),
        )

        conn.execute(
            """INSERT INTO data_fix_log
               (fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,
                new_value_summary,source,run_id)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                now, "price_history", f"bulk batch-interpolation repair {year}",
                candidates,
                "UPDATE price_history SET OHLCV = marcap raw values WHERE close non-integer",
                "close != FLOOR(close) (impossible for a real KRX print)",
                "marcap raw OHLCV, matched by (stock_code, date)",
                f"2026-03-31~04-07 batch incident, marcap cross-check - {year}", run_id,
            ),
        )
        conn.commit()
        result.update({"run_id": run_id})
        return result
    finally:
        conn.close()


if __name__ == "__main__":
    import json
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.year, dry_run=not args.apply), ensure_ascii=False, indent=2))
