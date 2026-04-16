"""
보유종목 종목코드 + 종목명 일괄 수정 스크립트
잘못된 코드와 이름을 올바르게 수정하고 주가를 백필합니다.

실행:
  cd /Applications/stock_dashboard && source venv/bin/activate
  python3 fix_portfolio_codes.py
"""
import sys
sys.path.insert(0, '/Applications/stock_dashboard')

from database import SessionLocal
from models import Portfolio, PortfolioTx
import yfinance as yf
import httpx, time

BASE = "http://127.0.0.1:8000"

# (현재 DB 코드, 올바른 코드, 올바른 이름)
# 코드와 이름이 모두 틀린 경우 포함
FIXES = [
    ("101160", "101160", "월덱스"),        # 코드 정상, 이름 수정 (일덱스→월덱스)
    ("425420", "036560", "티에프피"),       # 코드+이름 수정
    ("010820", "140670", "퍼스텍"),        # 코드 수정
    ("397030", "195990", "에이프릴바이오"), # 코드 수정
    ("475830", "331920", "오름테라퓨틱"),   # 코드 수정
    ("314130", "072520", "지놈앤컴퍼니"),   # 코드 수정
    ("398120", "160550", "에스지헬스케어"), # 코드 수정
    ("491000", "068760", "리브스메드"),     # 코드 수정
    ("389650", "195500", "넥스트바이오메디칼"),  # 코드+이름 수정 (네스트→넥스트)
    ("226950", "084990", "올릭스"),        # 코드 수정
    ("015260", "172670", "에이엘티"),      # 코드 수정 (기존 수정 재확인)
    ("089890", "089980", "코세스"),        # 코드 수정
    ("241770", "241770", "알덱스"),        # 이름 확인용
]

def has_price(code):
    try:
        r = httpx.get(f"{BASE}/api/dashboard/chart/{code}?days=30", timeout=5)
        return r.ok and len(r.json()) > 0
    except:
        return False

def fetch_price(code, name):
    for suffix in [".KQ", ".KS"]:
        try:
            df = yf.download(f"{code}{suffix}", period="1y", interval="1d", progress=False)
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
                           json={"stock_code": code, "prices": prices}, timeout=20)
            if r.status_code == 200:
                latest = sorted(prices, key=lambda x: x["date"])[-1]
                print(f"    {len(prices)}건 저장 / 최신가: {latest['close']:,.0f}원")
                return True
        except Exception as e:
            pass
    print(f"    ⚠️ Yahoo 데이터 없음")
    return False

def main():
    db = SessionLocal()

    print("=" * 60)
    print("보유종목 코드/이름 일괄 수정")
    print("=" * 60)

    changed = 0
    for old_code, new_code, new_name in FIXES:
        h = db.query(Portfolio).filter(Portfolio.stock_code == old_code).first()
        if not h:
            continue

        code_changed = old_code != new_code
        name_changed = h.stock_name != new_name

        if not code_changed and not name_changed:
            print(f"  ✅ {new_name}({new_code}): 정상")
            continue

        print(f"  수정: {h.stock_name}({old_code})", end="")
        if code_changed:
            print(f" → {new_name}({new_code})", end="")
        elif name_changed:
            print(f" → 이름: {new_name}", end="")
        print()

        h.stock_code = new_code
        h.stock_name = new_name

        # 거래내역도 수정
        db.query(PortfolioTx).filter(
            PortfolioTx.stock_code == old_code
        ).update({"stock_code": new_code, "stock_name": new_name})

        changed += 1

    db.commit()
    db.close()
    print(f"\n{changed}개 항목 수정 완료")

    # 백엔드 연결 확인
    try:
        httpx.get(f"{BASE}/", timeout=3)
    except:
        print("\n⚠️  백엔드가 꺼져있어 주가 백필을 건너뜁니다.")
        print("python3 run_server.py 실행 후 다시 시도하세요.")
        return

    # 수정된 종목 주가 백필
    print("\n주가 데이터 백필 중...")
    for old_code, new_code, new_name in FIXES:
        if old_code == new_code and not has_price(new_code):
            # 코드는 같지만 주가 없음
            print(f"  {new_name}({new_code})", end=" → ")
            fetch_price(new_code, new_name)
            time.sleep(0.5)
        elif old_code != new_code:
            print(f"  {new_name}({new_code})", end=" → ")
            if has_price(new_code):
                print("    주가 이미 있음")
            else:
                fetch_price(new_code, new_name)
            time.sleep(0.5)

    print("\n" + "=" * 60)
    print("완료! 브라우저에서 보유종목 탭을 새로고침하세요.")
    print("=" * 60)

if __name__ == "__main__":
    main()
