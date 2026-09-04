#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, '/Volumes/Realtek_NVME/stock_dashboard/runtime')
import main

DB='/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db'


def main_run():
    conn = sqlite3.connect(DB)
    try:
        rows = conn.execute(
            """
            SELECT ticker
            FROM us_stock_meta
            WHERE UPPER(COALESCE(country,''))='US'
              AND ticker IS NOT NULL AND ticker<>''
            ORDER BY CASE WHEN index_name='S&P500' THEN 0 ELSE 1 END, ticker
            """
        ).fetchall()
        tickers = [r[0] for r in rows]
    finally:
        conn.close()

    ok = 0
    fail = 0
    failed = []

    total = len(tickers)
    for i, tk in enumerate(tickers, start=1):
        try:
            main._refresh_us_stock_data(str(tk).upper())
            ok += 1
        except Exception as e:
            fail += 1
            if len(failed) < 200:
                failed.append({'ticker': tk, 'error': str(e)[:300]})
        if i % 50 == 0:
            print(f'progress {i}/{total} ok={ok} fail={fail}', flush=True)

    payload = {
        'run_at': datetime.now().isoformat(timespec='seconds'),
        'total': total,
        'ok': ok,
        'fail': fail,
        'failed_sample': failed,
    }
    out = f"/Volumes/Realtek_NVME/stock_dashboard/runtime/scratch/us_refresh_all_meta_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(out, flush=True)


if __name__ == '__main__':
    main_run()
