"""
scripts/ops/backfill_us_stale_prices_20260928.py

2026-09-28 사용자 지시("100% 완결로") 후속 — us_stock_meta(S&P500+NASDAQ) 중 최신 가격이
9/24 이전이거나 아예 없는 티커들을 yfinance로 재조회해 최신화한다. 대부분 소형주·SPAC·워런트라
실제로 상장폐지/거래정지된 경우가 섞여 있을 수 있음 — 그런 것들은 정직하게 "조회 불가"로 남기고
scratch 로그에 사유를 남긴다(억지로 채우지 않음).

실행: cd runtime && set -a; . ./.env; set +a; PYTHONPATH=runtime_pg_bootstrap:. venv/bin/python3 scripts/ops/backfill_us_stale_prices_20260928.py
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import yfinance as yf

from db_compat import connect_primary_db

LOG_PATH = "scratch/backfill_us_stale_prices_20260928.log"
WORKERS = 6
STALE_BEFORE = "2026-09-24"
_lock = threading.Lock()


def _log(ticker, status, detail=""):
    with _lock, open(LOG_PATH, "a") as f:
        f.write(f"{ticker}\t{status}\t{detail}\n")


def _fetch_and_save(conn_maker, ticker: str):
    conn = conn_maker()
    try:
        for attempt in range(3):
            try:
                df = yf.Ticker(ticker).history(period="3mo", interval="1d")
                if df is None or df.empty:
                    return ticker, "no_data", 0
                n = 0
                for idx, row in df.iterrows():
                    d = idx.strftime("%Y-%m-%d")
                    close = row.get("Close")
                    if close is None or close != close:
                        continue
                    conn.execute(
                        """INSERT INTO us_price_history (ticker,date,open,high,low,close,volume,created_at)
                           VALUES (?,?,?,?,?,?,?, now()::text)
                           ON CONFLICT (ticker,date) DO UPDATE SET
                             open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                             close=EXCLUDED.close, volume=EXCLUDED.volume""",
                        (ticker, d, float(row.get("Open") or 0) or None, float(row.get("High") or 0) or None,
                         float(row.get("Low") or 0) or None, float(close), float(row.get("Volume") or 0) or None),
                    )
                    n += 1
                conn.commit()
                return ticker, "ok", n
            except Exception as e:
                if "429" in str(e) or "Too Many" in str(e):
                    time.sleep(2 ** attempt * 3)
                    continue
                return ticker, "error", str(e)[:150]
        return ticker, "rate_limited_giveup", 0
    finally:
        conn.close()


def main():
    conn = connect_primary_db(timeout=30)
    rows = conn.execute("""
        SELECT m.ticker, MAX(h.date) mx
        FROM us_stock_meta m LEFT JOIN us_price_history h ON h.ticker=m.ticker AND h.close>0
        WHERE m.index_name IN ('S&P500','NASDAQ')
        GROUP BY m.ticker
        HAVING MAX(h.date) IS NULL OR MAX(h.date) < ?
    """, (STALE_BEFORE,)).fetchall()
    conn.close()
    targets = [r[0] for r in rows]
    print(f"대상 {len(targets)}개")

    ok = no_data = errors = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(_fetch_and_save, lambda: connect_primary_db(timeout=30), t): t for t in targets}
        n = 0
        for fut in as_completed(futures):
            ticker, status, detail = fut.result()
            n += 1
            _log(ticker, status, str(detail))
            if status == "ok":
                ok += 1
            elif status == "no_data":
                no_data += 1
            else:
                errors += 1
            if n % 20 == 0:
                print(f"[{n}/{len(targets)}] ok={ok} no_data={no_data} errors={errors}")

    print(f"완료: ok={ok} no_data={no_data} errors={errors}")


if __name__ == "__main__":
    main()
