#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
import sys

sys.path.insert(0, '/Volumes/Realtek_NVME/stock_dashboard/runtime')
import main


def main_run():
    conn = sqlite3.connect('/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db')
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT ticker
            FROM radar_market_cache
            WHERE UPPER(COALESCE(country,''))='US'
              AND ticker IS NOT NULL AND ticker<>''
            ORDER BY market_cap DESC
            """
        ).fetchall()
        tickers = [r[0] for r in rows]
    finally:
        conn.close()

    ok = 0
    fail = 0
    failed = []
    for i, tk in enumerate(tickers, start=1):
        try:
            main._refresh_us_stock_data(str(tk).upper())
            ok += 1
        except Exception as e:
            fail += 1
            failed.append({'ticker': tk, 'error': str(e)[:200]})
        if i % 10 == 0:
            print(f'progress {i}/{len(tickers)} ok={ok} fail={fail}')

    payload = {
        'run_at': datetime.now().isoformat(timespec='seconds'),
        'total': len(tickers),
        'ok': ok,
        'fail': fail,
        'failed': failed[:50],
    }
    out = f"/Volumes/Realtek_NVME/stock_dashboard/runtime/scratch/us_refresh_all_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(out)


if __name__ == '__main__':
    main_run()
