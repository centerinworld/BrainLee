#!/usr/bin/env python3
"""CashPortfolio 계약 테스트 (Codex C2 필수점검 포함, 2026-07-14)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from portfolio_engine import CashPortfolio


def test_fixed_limit():
    p = CashPortfolio(initial_cash=100_000_000, max_positions=10, fee_bps=0, slippage_bps=0, sell_tax_bps=0)
    assert p.buy('A', '2020-01-02', 10_000, budget=100_000_000)
    assert p.sell('A', '2020-02-03', 11_000)
    assert round(p.cash) == 110_000_000
    for i in range(10):
        assert p.buy(str(i), '2020-02-04', 10_000, budget=11_000_000)
    assert len(p.positions) == 10 and p.cash < 1
    print("fixed:", p.summary({str(i): 10_000 for i in range(10)}))


def test_dynamic_ticket_expansion():
    """C2 계약: 1.1억 에쿼티 → 11번째 1,000만원 티켓 허용, 1.1억 미만이면 거부."""
    # (a) 109,999,999원 → 11번째 거부
    p = CashPortfolio(initial_cash=109_999_999, max_positions=10,
                      fee_bps=0, slippage_bps=0, sell_tax_bps=0,
                      dynamic_tickets=True, ticket_budget=10_000_000)
    assert p.position_limit() == 10
    for i in range(10):
        assert p.buy(f"S{i}", '2020-01-02', 10_000, budget=10_000_000)
    mark = {f"S{i}": 10_000 for i in range(10)}
    assert not p.buy('S10', '2020-01-02', 10_000, budget=10_000_000, mark_prices=mark), \
        "1.1억 미만에서 11번째 티켓은 거부되어야 함"

    # (b) 정확히 110,000,000원 → 11번째 허용
    p2 = CashPortfolio(initial_cash=110_000_000, max_positions=10,
                       fee_bps=0, slippage_bps=0, sell_tax_bps=0,
                       dynamic_tickets=True, ticket_budget=10_000_000)
    assert p2.position_limit() == 11
    for i in range(10):
        assert p2.buy(f"T{i}", '2020-01-02', 10_000, budget=10_000_000)
    mark2 = {f"T{i}": 10_000 for i in range(10)}
    assert p2.buy('T10', '2020-01-02', 10_000, budget=10_000_000, mark_prices=mark2), \
        "1.1억 에쿼티에서 11번째 티켓은 허용되어야 함"
    assert len(p2.positions) == 11
    assert p2.cash >= 0, "현금 음수 금지"

    # (c) 미실현이익으로 한도 확장 (mark_prices 기반)
    p3 = CashPortfolio(initial_cash=100_000_000, max_positions=10,
                       fee_bps=0, slippage_bps=0, sell_tax_bps=0,
                       dynamic_tickets=True, ticket_budget=10_000_000)
    for i in range(9):
        assert p3.buy(f"U{i}", '2020-01-02', 10_000, budget=10_000_000)
    # 보유 9종목이 +20% 상승 → 에쿼티 1.18억 → 한도 11
    mark3 = {f"U{i}": 12_000 for i in range(9)}
    assert p3.position_limit(mark3) == 11
    print("dynamic: OK (10→11 확장·거부·미실현확장 전부 통과)")


def test_ledger_reconciliation():
    """원장 정합: 모든 현금 이동 합계 == 최종 현금."""
    p = CashPortfolio(initial_cash=100_000_000, fee_bps=15, slippage_bps=10)
    p.buy('A', '2020-01-02', 10_000, budget=10_000_000)
    p.sell('A', '2020-02-03', 10_000)  # 제로수익 왕복 → 비용만큼 순손실이어야 함
    assert p.cash < 100_000_000, "제로수익 왕복은 모델링된 비용만큼 음수여야 함"
    delta = 0.0
    for x in p.ledger:
        if x['side'] == 'buy':
            delta -= x['quantity'] * x['price'] + x['fee']
        else:
            delta += x['quantity'] * x['price'] - x['fee'] - x.get('tax', 0)
    assert abs((100_000_000 + delta) - p.cash) < 1e-6, "원장 합계와 최종 현금 불일치"
    print("ledger: OK (제로수익 왕복 비용 차감·원장 정합)")


if __name__ == '__main__':
    test_fixed_limit()
    test_dynamic_ticket_expansion()
    test_ledger_reconciliation()
    print("ALL PASS")
