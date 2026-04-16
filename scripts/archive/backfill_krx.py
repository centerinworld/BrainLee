"""
backfill_krx.py — KRX 3년치 지수 히스토리 즉시 수집 + 수급 테스트

실행:
  cd /Applications/stock_dashboard && source venv/bin/activate
  python3 backfill_krx.py
"""

import sys, httpx
sys.path.insert(0, '/Applications/stock_dashboard')

from krx_client import krx_client as krx
from datetime import datetime

BASE = "http://127.0.0.1:8000"

print("=" * 60)
print("KRX 데이터 수집 시작")
print("=" * 60)

# ── 1. KRX API 키 상태 확인 ────────────────────────────────
import config
api_key = getattr(config, "KRX_API_KEY", "")
print(f"\nKRX API 키: {'설정됨 (' + api_key[:8] + '...)' if api_key else '미설정 (데이터포털 fallback 사용)'}")

# ── 2. 코스피/코스닥 실시간 지수 테스트 ──────────────────────
print("\n[Step 1] 실시간 지수 테스트")
for mkt in ["kospi", "kosdaq"]:
    info = krx.get_index_realtime(mkt)
    print(f"  {mkt.upper()}: {info}")

# ── 3. 투자자별 수급 테스트 ──────────────────────────────────
print("\n[Step 2] 투자자별 수급 테스트")
for mkt in ["kospi", "kosdaq"]:
    inv = krx.get_index_investor(mkt)
    print(f"  {mkt.upper()} 수급: {inv}")

# ── 4. 코스피 3년치 히스토리 수집 ────────────────────────────
print("\n[Step 3] 코스피 3년치 히스토리 수집")
kospi_rows = krx.get_index_history("kospi", years=3)
print(f"  수집된 데이터: {len(kospi_rows)}건")
if kospi_rows:
    print(f"  첫번째: {kospi_rows[0]}")
    print(f"  마지막: {kospi_rows[-1]}")
    # DB 저장
    prices = [{
        "date": r["date"], "open": r["open"], "high": r["high"],
        "low": r["low"], "close": r["close"], "volume": r["volume"],
        "inst_net_buy": 0.0, "frn_net_buy": 0.0,
    } for r in kospi_rows]
    res = httpx.post(f"{BASE}/api/ingest/market-price",
                     json={"stock_code": "^KS11", "prices": prices}, timeout=60)
    print(f"  DB 저장: {res.status_code} → {res.json()}")

# ── 5. 코스닥 3년치 히스토리 수집 ────────────────────────────
print("\n[Step 4] 코스닥 3년치 히스토리 수집")
kosdaq_rows = krx.get_index_history("kosdaq", years=3)
print(f"  수집된 데이터: {len(kosdaq_rows)}건")
if kosdaq_rows:
    print(f"  첫번째: {kosdaq_rows[0]}")
    print(f"  마지막: {kosdaq_rows[-1]}")
    prices = [{
        "date": r["date"], "open": r["open"], "high": r["high"],
        "low": r["low"], "close": r["close"], "volume": r["volume"],
        "inst_net_buy": 0.0, "frn_net_buy": 0.0,
    } for r in kosdaq_rows]
    res = httpx.post(f"{BASE}/api/ingest/market-price",
                     json={"stock_code": "^KQ11", "prices": prices}, timeout=60)
    print(f"  DB 저장: {res.status_code} → {res.json()}")

# ── 6. 결과 확인 ─────────────────────────────────────────────
print("\n[Step 5] DB 저장 결과 확인")
for symbol, name in [("^KS11","KOSPI"),("^KQ11","KOSDAQ")]:
    r = httpx.get(f"{BASE}/api/dashboard/chart/{symbol}", params={"days": 1200}, timeout=10)
    count = len(r.json()) if r.ok else 0
    print(f"  {name}: DB {count}건")

print("\n완료! 백엔드를 재시작하면 종합 대시보드에 반영됩니다.")
print("  pkill -f 'uvicorn main'; uvicorn main:app --host 0.0.0.0 --port 8000 &")
