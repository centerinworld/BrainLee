"""
에이엘티(172670) 수정 스크립트 v3
- 015260으로 잘못 등록된 포트폴리오 → 172670으로 변경
- Yahoo Finance에서 172670 주가 즉시 백필

실행:
  cd /Applications/stock_dashboard && source venv/bin/activate
  python3 fix_aelt.py
"""
import sys, httpx, time
sys.path.insert(0, '/Applications/stock_dashboard')
BASE = "http://127.0.0.1:8000"

def main():
    try:
        httpx.get(f"{BASE}/", timeout=3)
    except:
        print("❌ 백엔드 서버를 먼저 실행하세요."); sys.exit(1)

    print("=" * 55)
    print("에이엘티(172670) 포트폴리오 + 주가 수정")
    print("=" * 55)

    # 현재 포트폴리오 조회
    holdings = httpx.get(f"{BASE}/api/portfolio", timeout=5).json()

    # 에이엔티(015260) 찾기
    target = next(
        (h for h in holdings if h["stock_code"] == "015260"
         or "에이엔티" in h.get("stock_name", "")), None
    )

    if target:
        old_code = target["stock_code"]
        qty      = target["quantity"]
        avg_p    = target["avg_price"]
        sector   = target.get("sector", "반도체")
        print(f"발견: {target['stock_name']}({old_code}) {qty:,.0f}주 @ {avg_p:,}원")

        # 기존 삭제
        r = httpx.delete(f"{BASE}/api/portfolio/{old_code}", timeout=5)
        print(f"  삭제({old_code}): {r.status_code}")
    else:
        # 이미 172670인지 확인
        aelt = next((h for h in holdings if h["stock_code"] == "172670"), None)
        if aelt:
            print(f"이미 172670으로 등록됨: {aelt['quantity']:,.0f}주 @ {aelt['avg_price']:,}원")
            qty, avg_p, sector = aelt["quantity"], aelt["avg_price"], aelt.get("sector","반도체")
        else:
            print("에이엔티/에이엘티 종목을 찾을 수 없습니다.")
            return

    # 172670으로 등록 (없는 경우만)
    aelt_now = next((h for h in httpx.get(f"{BASE}/api/portfolio",timeout=5).json()
                     if h["stock_code"]=="172670"), None)
    if not aelt_now:
        r = httpx.post(f"{BASE}/api/portfolio/transaction", json={
            "stock_code": "172670", "stock_name": "에이엘티",
            "sector": sector, "tx_type": "buy",
            "quantity": qty, "price": avg_p,
            "tx_date": "2026-03-22", "memo": "에이엘티 코드 수정",
        }, timeout=10)
        print(f"  172670 등록: {r.status_code}")

    # Yahoo Finance에서 172670 주가 백필
    print("\n172670 주가 수집 중...")
    import yfinance as yf
    success = False
    for suffix in [".KQ", ".KS"]:
        df = yf.download(f"172670{suffix}", period="1y", interval="1d", progress=False)
        if df.empty: continue
        prices = []
        for idx, row in df.iterrows():
            def g(c, r=row):
                v = r[c]; return float(v.iloc[0] if hasattr(v,'iloc') else v)
            prices.append({
                "date": str(idx), "open": g("Open"), "high": g("High"),
                "low": g("Low"), "close": g("Close"), "volume": g("Volume"),
                "inst_net_buy": 0.0, "frn_net_buy": 0.0,
            })
        r = httpx.post(f"{BASE}/api/ingest/market-price",
                       json={"stock_code":"172670","prices":prices}, timeout=20)
        print(f"  172670{suffix}: {len(prices)}건 → HTTP {r.status_code}")
        if r.status_code == 200:
            success = True; break

    if not success:
        print("  ⚠️  Yahoo에서 172670 데이터를 찾을 수 없습니다.")

    # watchlist 등록
    httpx.post(f"{BASE}/api/commands/analyze/172670", timeout=5)
    print("  watchlist 등록 완료")

    # 결과 확인
    print("\n최종 확인:")
    holdings = httpx.get(f"{BASE}/api/portfolio", timeout=5).json()
    aelt = next((h for h in holdings if h["stock_code"]=="172670"), None)
    if aelt:
        print(f"  ✅ 에이엘티(172670): {aelt['quantity']:,.0f}주 @ {aelt['avg_price']:,}원 현재가:{aelt['current_price']:,.0f}원")
    print("=" * 55)
    print("브라우저에서 보유종목 탭을 새로고침하세요.")

if __name__ == "__main__":
    main()
