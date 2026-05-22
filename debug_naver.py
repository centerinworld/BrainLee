#!/usr/bin/env python3
"""네이버 증권 해외지수 API 응답 디버깅 + 5.8일 데이터 직접 삽입"""
import requests, json, sqlite3
from datetime import datetime, timedelta, date

headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://finance.naver.com/",
}

end_dt   = datetime.now()
start_dt = end_dt - timedelta(days=10)
start_str = start_dt.strftime("%Y%m%d") + "000000"
end_str   = end_dt.strftime("%Y%m%d") + "235959"

results = {}

for naver_sym, yf_sym, name in [("NAS", "^IXIC", "NASDAQ"), ("SPI", "^GSPC", "S&P500")]:
    print(f"\n{'='*50}")
    print(f"[{name}] 차트 API 호출 중...")
    url = f"https://api.stock.naver.com/chart/world/{naver_sym}/day?startDateTime={start_str}&endDateTime={end_str}"
    try:
        r = requests.get(url, headers=headers, timeout=15)
        print(f"  Status: {r.status_code}")
        raw = r.text[:2000]
        print(f"  Raw (2000자): {raw}")
        
        if r.status_code == 200:
            data = r.json()
            print(f"  Type: {type(data)}")
            if isinstance(data, list):
                print(f"  항목 수: {len(data)}")
                if data:
                    print(f"  첫 항목: {data[0]}")
                    print(f"  마지막 항목: {data[-1]}")
                    results[yf_sym] = data
            elif isinstance(data, dict):
                print(f"  Dict keys: {list(data.keys())}")
                # 다른 키로 리스트가 들어있을 수 있음
                for k, v in data.items():
                    if isinstance(v, list) and v:
                        print(f"  Key '{k}' 에 {len(v)}개 항목")
                        print(f"  첫 항목: {v[0]}")
    except Exception as e:
        print(f"  오류: {e}")

print("\n\n=== DB 직접 삽입 (파싱 성공한 경우) ===")
if results:
    conn = sqlite3.connect("/Applications/stock_dashboard/stock.db")
    for yf_sym, data in results.items():
        inserted = 0
        for item in data:
            # 가능한 모든 키 이름 시도
            dt_str = str(item.get("localDate") or item.get("date") or item.get("dt") or "")
            if len(dt_str) == 8:
                d_iso = f"{dt_str[:4]}-{dt_str[4:6]}-{dt_str[6:8]}"
            elif len(dt_str) >= 10 and "-" in dt_str:
                d_iso = dt_str[:10]
            else:
                continue

            c = float(item.get("closePrice") or item.get("close") or item.get("clsprc") or 0)
            o = float(item.get("openPrice")  or item.get("open")  or item.get("opnprc") or c)
            h = float(item.get("highPrice")  or item.get("high")  or item.get("hgprc")  or c)
            l = float(item.get("lowPrice")   or item.get("low")   or item.get("lwprc")  or c)
            v = float(item.get("accTradeVolume") or item.get("volume") or 0)
            if c <= 0:
                continue
            conn.execute("""
                INSERT INTO price_history (stock_code, date, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(stock_code, date) DO UPDATE SET
                    open=excluded.open, high=excluded.high, low=excluded.low,
                    close=excluded.close, volume=excluded.volume
            """, (yf_sym, d_iso, o, h, l, c, v))
            inserted += 1
        conn.commit()
        print(f"{yf_sym}: {inserted}건 삽입/갱신 완료")
    conn.close()
else:
    print("수집된 데이터 없음 - 수동 입력 모드")
    print("\n아래 명령으로 5.8일 종가를 수동 입력할 수 있습니다:")
    print("  python3 insert_manual.py")
    
    # 수동 삽입 스크립트 생성
    manual = '''
import sqlite3
conn = sqlite3.connect("/Applications/stock_dashboard/stock.db")
# 5월 8일 (목) 종가 - 확인 후 수정하세요
data = [
    # (symbol, date, open, high, low, close, volume)
    # 아래 숫자는 확인이 필요합니다
    ("^IXIC", "2026-05-08", 25970.0, 26100.0, 25900.0, 26060.0, 0),
    ("^GSPC", "2026-05-08", 7378.0, 7420.0, 7360.0, 7415.0, 0),
]
for row in data:
    conn.execute("""
        INSERT INTO price_history (stock_code, date, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(stock_code, date) DO UPDATE SET
            close=excluded.close, open=excluded.open,
            high=excluded.high, low=excluded.low
    """, row)
conn.commit()
conn.close()
print("삽입 완료")
'''
    with open("/Applications/stock_dashboard/insert_manual.py", "w") as f:
        f.write(manual)
    print("insert_manual.py 생성됨")

print("\n=== DB 최신 상태 ===")
conn = sqlite3.connect("/Applications/stock_dashboard/stock.db")
rows = conn.execute(
    "SELECT stock_code, date, close FROM price_history WHERE stock_code IN ('^IXIC','^GSPC') ORDER BY stock_code, date DESC LIMIT 6"
).fetchall()
for r in rows:
    print(f"  {r}")
conn.close()
