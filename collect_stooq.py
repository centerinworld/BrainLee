#!/usr/bin/env python3
"""
stooq.com에서 나스닥/S&P500 히스토리 수집 후 DB 직접 삽입
Yahoo Finance / 네이버 API 모두 막힌 경우 사용
"""
import requests, csv, sqlite3, io
from datetime import datetime, timedelta, date

headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

# stooq 심볼 매핑
STOOQ_MAP = {
    "^IXIC": ("^ixic", "NASDAQ"),    # NASDAQ Composite
    "^GSPC": ("^spx",  "S&P500"),    # S&P 500
    "^VIX":  ("^vix",  "VIX"),       # VIX
}

start_date = "20260501"
end_date   = datetime.now().strftime("%Y%m%d")

conn = sqlite3.connect("/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db")
total_inserted = 0

for yf_sym, (stooq_sym, name) in STOOQ_MAP.items():
    url = f"https://stooq.com/q/d/l/?s={stooq_sym}&d1={start_date}&d2={end_date}&i=d"
    print(f"\n[{name}] 수집 중...")
    print(f"  URL: {url}")
    try:
        r = requests.get(url, headers=headers, timeout=15)
        print(f"  Status: {r.status_code}")
        print(f"  응답 첫 200자: {r.text[:200]}")

        if r.status_code != 200:
            print(f"  → 실패")
            continue

        # CSV 파싱
        reader = csv.DictReader(io.StringIO(r.text))
        rows = list(reader)
        print(f"  CSV 컬럼: {reader.fieldnames}")
        print(f"  행 수: {len(rows)}")

        if not rows:
            print(f"  → 데이터 없음")
            continue

        inserted = 0
        for row in rows:
            try:
                # stooq 컬럼: Date, Open, High, Low, Close, Volume
                d_iso = row.get("Date", "").strip()
                c = float(row.get("Close", 0) or 0)
                o = float(row.get("Open",  0) or c)
                h = float(row.get("High",  0) or c)
                l = float(row.get("Low",   0) or c)
                v = float(row.get("Volume", 0) or 0)
                if not d_iso or c <= 0:
                    continue
                conn.execute("""
                    INSERT INTO price_history (stock_code, date, open, high, low, close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(stock_code, date) DO UPDATE SET
                        open=excluded.open, high=excluded.high, low=excluded.low,
                        close=excluded.close, volume=excluded.volume
                """, (yf_sym, d_iso, o, h, l, c, v))
                inserted += 1
            except Exception as e:
                print(f"  행 파싱 오류: {e} | {row}")

        conn.commit()
        total_inserted += inserted
        print(f"  → {inserted}건 DB 저장 완료")

    except Exception as e:
        print(f"  오류: {e}")

conn.close()

print(f"\n=== 완료: 총 {total_inserted}건 저장 ===")

# 결과 확인
conn = sqlite3.connect("/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db")
print("\n[DB 최신 상태]")
rows = conn.execute("""
    SELECT stock_code, date, close FROM price_history
    WHERE stock_code IN ('^IXIC', '^GSPC', '^VIX')
    ORDER BY stock_code, date DESC LIMIT 9
""").fetchall()
for r in rows:
    print(f"  {r[0]:8s}  {r[1]}  {r[2]:,.2f}")
conn.close()
