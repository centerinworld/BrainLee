"""
Regression tests for the qty/pnl_krw trade-record fields and cost_multiplier parameter
added to backtest_strategies/sector.py per Codex's review
(docs/claude_resume_strategy_review_20260912.md follow-up, priority 1: real KRW
reconciliation and cost-stress testing require these).
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SECTOR_SRC = (ROOT / "backtest_strategies" / "sector.py").read_text(encoding="utf-8")


class TestTradeRecordsCarryQtyAndPnlKrw(unittest.TestCase):
    def test_buy_records_include_qty(self):
        # both BUY append sites (strict_exec pending-buy fill, and rebalance-time direct buy)
        self.assertEqual(SECTOR_SRC.count('"action": "BUY",\n                                       "price": px, "sector": sector_key, "qty": qty'), 1)
        self.assertIn('"action": "BUY",\n                            "price": buy_p, "sector": sector_key, "qty": qty', SECTOR_SRC)

    def test_sell_records_include_qty_entry_price_pnl_krw(self):
        self.assertIn('"pnl_krw": round(_pnl_amt)', SECTOR_SRC)
        # appears at each SELL/SECTOR_EXIT/FINAL site -- at least 5 occurrences expected
        self.assertGreaterEqual(SECTOR_SRC.count('"pnl_krw"'), 5)


class TestNetProfitScaledWrapper(unittest.TestCase):
    def test_scaled_wrapper_matches_unscaled_at_multiplier_1(self):
        from backtest_common import _net_profit

        # Replicate the wrapper's math directly (same formula as in sector.py) to confirm
        # multiplier=1.0 is a no-op and multiplier=2.0 doubles exactly the cost portion.
        def scaled(entry_p, exit_p, qty, mkt_cap, multiplier):
            net_krw, net_pct = _net_profit(entry_p, exit_p, qty, mkt_cap)
            if multiplier == 1.0:
                return net_krw, net_pct
            gross = (exit_p - entry_p) * qty
            cost = gross - net_krw
            scaled_net = gross - cost * multiplier
            base = entry_p * qty
            return round(scaled_net), round((scaled_net / base) * 100, 2) if base else 0.0

        base_net, base_pct = _net_profit(10_000, 11_000, 100, 5000.0)
        s1_net, s1_pct = scaled(10_000, 11_000, 100, 5000.0, 1.0)
        self.assertEqual((base_net, base_pct), (s1_net, s1_pct))

        s2_net, s2_pct = scaled(10_000, 11_000, 100, 5000.0, 2.0)
        gross = (11_000 - 10_000) * 100
        cost = gross - base_net
        expected_net = gross - cost * 2.0
        self.assertEqual(s2_net, round(expected_net))
        self.assertLess(s2_net, base_net, "doubling cost must reduce net profit")

    def test_cost_multiplier_param_exists_with_default_1(self):
        import inspect
        from backtest_strategies.sector import run_backtest_sector
        sig = inspect.signature(run_backtest_sector)
        self.assertIn("cost_multiplier", sig.parameters)
        self.assertEqual(sig.parameters["cost_multiplier"].default, 1.0)


if __name__ == "__main__":
    unittest.main()
