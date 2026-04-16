"""
에이엔티(015260) → 에이엘티(172670) 수정 스크립트

실행:
  cd /Applications/stock_dashboard && source venv/bin/activate
  python3 fix_portfolio.py
"""
import sys, httpx
sys.path.insert(0, '/Applications/stock_dashboard')

BASE = "http://127.0.0.1:8000"

def main():
    print("에이엔티 → 에이엘티 수정")

    # 1. 에이엔티(015260) 삭제
    res = httpx.delete(f"{BASE}/api/portfolio/015260", timeout=5)
    print(f"  015260 삭제: HTTP {res.status_code}")

    # 2. 에이엘티(172670) 재등록 (매입가 12,301원, 수량 46,698주)
    res = httpx.post(f"{BASE}/api/portfolio/transaction", json={
        "stock_code": "172670",
        "stock_name": "에이엘티",
        "sector":     "반도체",
        "tx_type":    "buy",
        "quantity":   46698.0,
        "price":      12301.0,
        "tx_date":    "2026-03-22",
        "memo":       "에이엔티→에이엘티 수정",
    }, timeout=5)
    print(f"  172670 에이엘티 등록: HTTP {res.status_code}")
    if res.status_code == 200:
        print("  완료! 브라우저에서 보유종목 탭을 새로고침하세요.")
    else:
        print(f"  오류: {res.text}")

if __name__ == "__main__":
    main()
