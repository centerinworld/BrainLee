"""
Regression tests for the partial-sell quantity loss Codex found in review
(docs/claude_resume_strategy_review_20260912.md priority 1): a sector.py partial
take-profit event (action=SELL, partial_qty=30, remaining_qty=70 out of 100 held) used to
degrade into a full 100-share sale once replayed through merged_simulator.py, because
CandidateOrder carried no quantity/fraction field and CashPortfolio.sell() always closes
the whole position.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from portfolio_engine import CashPortfolio  # noqa: E402
from merged_simulator import (  # noqa: E402
    CandidateOrder, MergeConfig, orders_from_trades_json, simulate_merged_account,
)


class TestCashPortfolioSellPartial(unittest.TestCase):
    def test_sell_partial_keeps_remainder_open(self):
        p = CashPortfolio(initial_cash=100_000_000, fee_bps=0, slippage_bps=0, sell_tax_bps=0)
        p.buy("A", "2024-01-04", 10_000, budget=1_000_000)  # 100 shares
        bought_qty = p.positions["A"].quantity
        self.assertEqual(bought_qty, 100)
        ok = p.sell_partial("A", "2024-01-05", 11_000, 0.3, "부분익절")
        self.assertTrue(ok)
        self.assertIn("A", p.positions, "70%% must remain open, not be fully closed")
        self.assertEqual(p.positions["A"].quantity, 70)

    def test_sell_partial_realizes_proportional_cost_basis(self):
        p = CashPortfolio(initial_cash=100_000_000, fee_bps=0, slippage_bps=0, sell_tax_bps=0)
        p.buy("A", "2024-01-04", 10_000, budget=1_000_000)  # 100 @ 10,000, cost_basis=1,000,000
        p.sell_partial("A", "2024-01-05", 12_000, 0.3, "부분익절")
        # 30 shares sold at 12,000 = 360,000 proceeds; cost basis released = 300,000 (30%% of 1,000,000)
        remaining = p.positions["A"]
        self.assertEqual(remaining.quantity, 70)
        self.assertAlmostEqual(remaining.cost_basis, 700_000.0, places=2)
        sell_row = [r for r in p.ledger if r["side"] == "sell"][0]
        self.assertEqual(sell_row["quantity"], 30)
        self.assertAlmostEqual(sell_row["pnl"], 360_000 - 300_000, places=2)

    def test_fraction_none_or_full_behaves_like_full_sell(self):
        p = CashPortfolio(initial_cash=100_000_000, fee_bps=0, slippage_bps=0, sell_tax_bps=0)
        p.buy("A", "2024-01-04", 10_000, budget=1_000_000)
        p.sell_partial("A", "2024-01-05", 11_000, None, "signal")
        self.assertNotIn("A", p.positions)


class TestMergedSimulatorPreservesPartialSell(unittest.TestCase):
    def test_partial_tp_event_does_not_become_full_sale(self):
        """Exact repro of Codex's reported case: 100 shares bought, a SELL event carrying
        partial_qty=30/remaining_qty=70, replayed through simulate_merged_account -- must
        leave 70 shares open, not fully liquidate."""
        buy = CandidateOrder("2024-01-04", "AAAAAA", "buy", 10_000, "sector_focus", 1.0, budget=1_000_000)
        sell_fraction = 30 / (30 + 70)
        partial_sell = CandidateOrder("2024-01-05", "AAAAAA", "sell", 11_000, "sector_focus", 1.0,
                                       reason="부분익절", sell_fraction=sell_fraction)
        cfg = MergeConfig(initial_cash=100_000_000, ticket_budget=1_000_000, max_positions=10,
                           dynamic_tickets=False, fee_bps=0, slippage_bps=0, sell_tax_bps=0)
        result = simulate_merged_account([buy, partial_sell], cfg)
        # position must still be open with the remainder, not closed
        sell_events = [e for e in result["events"] if e["side"] == "sell" and e["status"] == "filled"]
        self.assertEqual(len(sell_events), 1)
        self.assertTrue(sell_events[0].get("partial"), "must be flagged as a partial fill")
        self.assertEqual(sell_events[0]["remaining_qty"], 70)
        self.assertEqual(result["summary"]["open_positions"], 1, "70 shares must remain an open position")

    def test_orders_from_trades_json_extracts_sell_fraction_from_partial_qty(self):
        raw = json.dumps({"trades": [
            {"action": "BUY", "date": "2024-01-04", "code": "AAAAAA", "price": 10_000},
            {"action": "SELL", "date": "2024-01-05", "code": "AAAAAA", "price": 11_000,
             "reason": "부분익절", "partial_qty": 30, "remaining_qty": 70},
        ]})
        orders = orders_from_trades_json("sector_focus", raw)
        sell_order = [o for o in orders if o.side == "sell"][0]
        self.assertAlmostEqual(sell_order.sell_fraction, 0.3, places=6)

    def test_orders_from_trades_json_full_sell_has_no_fraction(self):
        raw = json.dumps({"trades": [
            {"action": "BUY", "date": "2024-01-04", "code": "AAAAAA", "price": 10_000},
            {"action": "SELL", "date": "2024-01-05", "code": "AAAAAA", "price": 11_000, "reason": "손절"},
        ]})
        orders = orders_from_trades_json("sector_focus", raw)
        sell_order = [o for o in orders if o.side == "sell"][0]
        self.assertIsNone(sell_order.sell_fraction)


if __name__ == "__main__":
    unittest.main()
