"""
collect_supply.py — pykrx로 코스피/코스닥 당일 수급 즉시 수집

실행:
  cd /Applications/stock_dashboard && source venv/bin/activate
  python3 collect_supply.py
"""
import sys, httpx
sys.path.insert(0, '/Applications/stock_dashboard')

from datetime import date
from pykrx import stock as pykrx

BASE      = "http://127.0.0.1:8000"
today_str = date.today().strftime("%Y%m%d")
today_iso = date.today().isoformat()

print(f"pykrx 수급 수집: {today_iso}")

for market, symbol in [("KOSPI", "^KS11"), ("KOSDAQ", "^KQ11")]:
    try:
        df = pykrx.get_market_net_purchases_of_business_day(today_str, today_str, market)
        if df is None or df.empty:
            print(f"  {market}: 데이터 없음")
            continue

        print(f"\n  {market} 컬럼: {list(df.columns)}")
        print(f"  {market} 인덱스: {list(df.index)}")

        # 순매수거래대금 컬럼 찾기
        net_col = None
        for col in df.columns:
            if "순매수" in str(col) and "거래대금" in str(col):
                net_col = col
                break
        if net_col is None:
            net_col = df.columns[-1]
        print(f"  사용 컬럼: {net_col}")

        inst = frn = ind = 0.0
        for idx_val in df.index:
            s   = str(idx_val)
            val = float(df.loc[idx_val, net_col]) / 100  # 백만→억원
            print(f"    {s}: {val:+,.1f}억원")
            if "기관" in s and "합계" in s:
                inst = val
            elif "외국인" in s and "기타" not in s:
                frn  = val
            elif "개인" in s:
                ind  = val

        print(f"  결과 → 기관={inst:+,.1f} 외국인={frn:+,.1f} 개인={ind:+,.1f}억원")

        if inst != 0 or frn != 0:
            r = httpx.post(f"{BASE}/api/ingest/investor-trends",
                json={"stock_code": symbol, "trends": [{
                    "date":         today_iso,
                    "inst_net_buy": round(inst, 1),
                    "frn_net_buy":  round(frn,  1),
                }]}, timeout=10)
            print(f"  DB 저장: {r.status_code}")
        else:
            print(f"  수급 0 → 저장 안 함")

    except Exception as e:
        print(f"  {market} 오류: {e}")
        import traceback; traceback.print_exc()

print("\n완료! 브라우저 새로고침 후 수급 확인하세요.")
