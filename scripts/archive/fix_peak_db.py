"""
fix_peak_db.py — Peak DB 잘못된 데이터 수정

문제: 섹터없는 종목(ISC, 두산테스나, 브이엠, HPSP)이 잘못 파싱됨
  - id=17: [220,000] 섹터=ISC  (종목명↔매수가 뒤바뀜)
  - id=18: [72,900]  섹터=두산테스나
  - id=19: [45,500]  섹터=브이엠
  - id=20: [50,000]  섹터=HPSP

처리:
  1. id=17,18,19,20 삭제
  2. id=5,6,10,11 (올바르게 저장된 것)은 유지
"""

import sys
sys.path.insert(0, '/Applications/stock_dashboard')
from database import SessionLocal
import models

db = SessionLocal()

print("=== 수정 전 상태 ===")
for h in db.query(models.PeakHolding).all():
    print(f"  id={h.id} [{h.stock_name}] 섹터={h.sector} 보유일={h.hold_days} 활성={h.sold_at is None}")

# ── 잘못된 데이터 삭제 (id=17,18,19,20) ──────────────────────
bad_ids = [17, 18, 19, 20]
for bad_id in bad_ids:
    # PeakTrade 먼저 삭제
    db.query(models.PeakTrade).filter(
        models.PeakTrade.holding_id == bad_id
    ).delete()
    # PeakHolding 삭제
    db.query(models.PeakHolding).filter(
        models.PeakHolding.id == bad_id
    ).delete()
    print(f"  삭제: id={bad_id}")

db.commit()

print("\n=== 수정 후 상태 ===")
holdings = db.query(models.PeakHolding).all()
print(f"총 {len(holdings)}건")
for h in holdings:
    status = "보유중" if h.sold_at is None else "매도완료"
    print(f"  id={h.id} [{h.stock_name}] 섹터={h.sector}({h.sector_score}) 보유일={h.hold_days}일 [{status}]")

db.close()
print("\n완료! peak_monitor.py를 재시작하세요.")
