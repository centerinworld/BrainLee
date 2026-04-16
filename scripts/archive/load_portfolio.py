"""
보유종목 초기 데이터 일괄 입력 스크립트 v2
ticker_mapper로 종목코드를 먼저 조회한 후 API에 전송합니다.

실행:
  cd /Applications/stock_dashboard && source venv/bin/activate
  python3 load_portfolio.py
"""

import sys
sys.path.insert(0, '/Applications/stock_dashboard')

import httpx, time
from ticker_utils import ticker_mapper

BASE = "http://127.0.0.1:8000"

# (종목명, 매입가, 수량, 섹터)
HOLDINGS = [
    # ── 반도체 ─────────────────────────────────────────
    ("에이엔티",           12301,  46698, "반도체"),
    ("에스에이엠티",       5237,   6830,  "반도체"),
    ("아이엠티",           12842,  1500,  "반도체"),
    ("티에스이",           61857,  200,   "반도체"),
    ("에스티아이",         22830,  500,   "반도체"),
    ("예스티",             18180,  610,   "반도체"),
    ("오킨스전자",         9979,   1177,  "반도체"),
    ("씨이맥스",           21359,  361,   "반도체"),
    ("샘씨엔에스",         4688,   1500,  "반도체"),
    ("티에프피",           42550,  100,   "반도체"),
    ("코세스",             25600,  150,   "반도체"),
    ("일덱스",             30350,  70,    "반도체"),
    ("네오티스",           10970,  200,   "반도체"),
    # ── 바이오 ─────────────────────────────────────────
    ("앱클론",             28635,  850,   "바이오"),
    ("리가켐바이오",       143519, 162,   "바이오"),
    ("에이프릴바이오",     22273,  800,   "바이오"),
    ("오름테라퓨틱",       103131, 147,   "바이오"),
    ("일동제약",           31930,  447,   "바이오"),
    ("지놈앤컴퍼니",       7835,   1747,  "바이오"),
    ("에이비엘바이오",     195918, 66,    "바이오"),
    ("에스지헬스케어",     3755,   3042,  "바이오"),
    ("네스트바이오메디칼", 72267,  90,    "바이오"),
    ("올릭스",             187000, 30,    "바이오"),
    ("리브스메드",         58919,  37,    "바이오"),
    # ── 기타 ───────────────────────────────────────────
    ("퍼스텍",             4705,   1000,  "방산"),
    ("팬오션",             5076,   1000,  "해운"),
    ("조이시티",           2710,   468,   "게임"),
    ("한화솔루션",         38350,  300,   "태양광"),
    ("AJ네트웍스",        4650,   990,   "부동산"),
]

def main():
    print("=" * 58)
    print("보유종목 초기 데이터 일괄 입력")
    print("=" * 58)
    try:
        httpx.get(f"{BASE}/", timeout=3)
    except Exception:
        print("백엔드 서버 연결 실패. run_server.py 먼저 실행하세요.")
        return

    success = fail = 0
    cur_sector = None

    for name, avg_price, quantity, sector in HOLDINGS:
        if sector != cur_sector:
            print(f"\n── {sector} ──")
            cur_sector = sector

        # 종목코드 조회
        code = ticker_mapper.get_code(name)
        if not code:
            results = ticker_mapper.search(name)
            if results:
                code = results[0].get("code")
                rname = results[0].get("name", name)
                print(f"  검색 매칭: {name} → {rname} ({code})")
            else:
                print(f"  SKIP {name}: 종목코드 없음")
                fail += 1
                continue

        try:
            res = httpx.post(f"{BASE}/api/portfolio/transaction", json={
                "stock_code": code,
                "stock_name": name,
                "sector":     sector,
                "tx_type":    "buy",
                "quantity":   float(quantity),
                "price":      float(avg_price),
                "tx_date":    "2026-03-22",
                "memo":       "초기 일괄 입력",
            }, timeout=10)
            if res.status_code == 200:
                print(f"  OK  {name}({code})  {quantity:,}주 @ {avg_price:,}원")
                success += 1
            else:
                print(f"  ERR {name}: {res.text[:60]}")
                fail += 1
        except Exception as e:
            print(f"  ERR {name}: {e}")
            fail += 1

        time.sleep(0.1)

    print(f"\n{'='*58}")
    print(f"완료: 성공 {success} / 실패 {fail}")
    print("=" * 58)

if __name__ == "__main__":
    main()
