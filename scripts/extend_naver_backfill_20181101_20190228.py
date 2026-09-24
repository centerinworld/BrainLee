#!/usr/bin/env python3
"""Extend naver_price_history_backfill through the 2019-01-02 boundary.

Read-only w.r.t. price_history - only stages into naver_price_history_backfill
(INSERT OR IGNORE) so the existing 2015-2018 backfill data is never touched.
Reuses the same date-bounded fchart fetch as backfill_naver_ohlcv_2015_2018.py
(the count=7000 cap workaround already fixed there).
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402
from backfill_naver_ohlcv_2015_2018 import fetch  # noqa: E402

START, END = "20181101", "20190228"


def main() -> None:
    conn = connect_primary_db(timeout=180)
    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM stock_universe WHERE stock_code ~ '^[0-9]{6}$' ORDER BY stock_code"
    ).fetchall()]
    fetched, errors, staged = 0, 0, 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(fetch, code, START, END) for code in codes]
        for future in as_completed(futures):
            _, rows, error = future.result()
            fetched += 1
            errors += bool(error)
            if rows:
                conn.executemany(
                    """INSERT OR IGNORE INTO naver_price_history_backfill
                       (stock_code,date,open,high,low,close,volume,source_url,fetched_at)
                       VALUES(?,?,?,?,?,?,?,?,?)""", rows,
                )
                staged += len(rows)
            if fetched % 500 == 0:
                conn.commit()
                print(f"progress {fetched}/{len(codes)} staged={staged:,} errors={errors}", flush=True)
    conn.commit()
    print({"codes": len(codes), "request_errors": errors, "staged_rows": staged,
           "finished_at": datetime.now().isoformat(timespec="seconds")}, flush=True)
    conn.close()


if __name__ == "__main__":
    main()
