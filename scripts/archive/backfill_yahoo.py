"""
backfill_yahoo.py — Yahoo Finance로 지수/매크로 3년치 즉시 수집

실행:
  cd /Applications/stock_dashboard && source venv/bin/activate
  python3 backfill_yahoo.py
"""
import sys, time
import httpx
import yfinance as yf
import pandas as pd
from datetime import datetime, date

sys.path.insert(0, '/Applications/stock_dashboard')

BASE = "http://127.0.0.1:8000"
today_iso = date.today().isoformat()
now_str   = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

SYMBOLS = [
    ("^KS11",    "KOSPI",    "3y"),
    ("^KQ11",    "KOSDAQ",   "3y"),
    ("^VIX",     "VIX",      "3y"),
    ("USDKRW=X", "USD/KRW",  "3y"),
    ("GC=F",     "GOLD",     "3y"),
    ("CL=F",     "OIL",      "3y"),
]

def gv(col, row):
    v = row.get(col, 0) if hasattr(row, 'get') else getattr(row, col, 0)
    if isinstance(v, pd.Series): v = v.iloc[0]
    try:    return float(v) if v is not None else 0.0
    except: return 0.0

print("=" * 60)
print("Yahoo Finance 3년치 수집 시작")
print("=" * 60)

for symbol, name, period in SYMBOLS:
    print(f"\n[{name}] ({symbol}) period={period}")
    try:
        df = yf.download(symbol, period=period, interval="1d",
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            print(f"  데이터 없음")
            continue

        if hasattr(df.columns, 'get_level_values'):
            try: df.columns = df.columns.get_level_values(0)
            except: pass

        prices = []
        for ts, row in df.iterrows():
            try:
                row_date = ts.date() if hasattr(ts, 'date') else date.fromisoformat(str(ts)[:10])
                close = gv("Close", row)
                if close <= 0:
                    continue
                prices.append({
                    "date":         row_date.isoformat(),
                    "open":         gv("Open",   row),
                    "high":         gv("High",   row),
                    "low":          gv("Low",    row),
                    "close":        close,
                    "volume":       gv("Volume", row),
                    "inst_net_buy": 0.0,
                    "frn_net_buy":  0.0,
                })
            except Exception as e:
                continue

        if not prices:
            print(f"  파싱 결과 없음")
            continue

        # 오늘 날짜 없으면 최신값을 오늘 날짜+현재시각으로 추가
        if not any(p["date"] == today_iso for p in prices):
            latest = max(prices, key=lambda x: x["date"])
            prices.append({**latest, "date": now_str})

        # DB 저장
        res = httpx.post(f"{BASE}/api/ingest/market-price",
                         json={"stock_code": symbol, "prices": prices},
                         timeout=60)
        latest = max(prices, key=lambda x: x["date"])
        print(f"  {len(prices)}건 수집 → DB 저장: {res.status_code}")
        print(f"  최신: {latest['date']} close={latest['close']:,.2f}")

        time.sleep(0.5)

    except Exception as e:
        print(f"  오류: {e}")

# ── 결과 확인 ──────────────────────────────────────────────────
print("\n" + "=" * 60)
print("DB 저장 결과 확인")
print("=" * 60)
for symbol, name, _ in SYMBOLS:
    try:
        r = httpx.get(f"{BASE}/api/dashboard/chart/{symbol}",
                      params={"days": 1200}, timeout=10)
        data = r.json() if r.status_code == 200 else []
        count = len(data)
        first = data[0]["date"]  if count > 0 else "-"
        last  = data[-1]["date"] if count > 0 else "-"
        print(f"  {name:10s}: {count:4d}건  {first} ~ {last}")
    except Exception as e:
        print(f"  {name}: 오류 {e}")

print("\n완료! 백엔드를 재시작하세요:")
print("  pkill -f 'uvicorn main'; sleep 1; uvicorn main:app --host 0.0.0.0 --port 8000 &")
