"""Regression tests for F09 (sell-ownership policy, docs/claude_handoff_strategy_code_findings_20260912.md)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from merged_simulator import CandidateOrder, MergeConfig, simulate_merged_account  # noqa: E402


class TestSellOwnershipPolicy(unittest.TestCase):
    def _run(self, policy):
        orders = [
            CandidateOrder("2024-01-04", "AAAAAA", "buy", 10_000, "strat_A", 1.0),
            # strat_B never bought A -- issues a sell signal for it anyway
            CandidateOrder("2024-01-05", "AAAAAA", "sell", 11_000, "strat_B", 1.0),
        ]
        cfg = MergeConfig(initial_cash=100_000_000, fee_bps=0, slippage_bps=0, sell_tax_bps=0,
                           sell_ownership_policy=policy)
        return simulate_merged_account(orders, cfg)

    def test_any_sell_default_matches_prior_behavior(self):
        result = self._run("any_sell")
        sell_events = [e for e in result["events"] if e["side"] == "sell"]
        self.assertEqual(sell_events[0]["status"], "filled",
                          "default policy must accept a non-owner strategy's sell (unchanged behavior)")
        self.assertEqual(result["summary"]["completed_trades"], 1)

    def test_owner_only_rejects_non_owner_sell(self):
        result = self._run("owner_only")
        sell_events = [e for e in result["events"] if e["side"] == "sell"]
        self.assertEqual(sell_events[0]["status"], "rejected")
        self.assertEqual(sell_events[0]["reason"], "not_owner_strategy")
        self.assertEqual(result["summary"]["completed_trades"], 0,
                          "position must remain open since only a non-owner strategy tried to sell it")

    def test_owner_only_accepts_owner_sell(self):
        orders = [
            CandidateOrder("2024-01-04", "AAAAAA", "buy", 10_000, "strat_A", 1.0),
            CandidateOrder("2024-01-05", "AAAAAA", "sell", 11_000, "strat_A", 1.0),
        ]
        cfg = MergeConfig(initial_cash=100_000_000, fee_bps=0, slippage_bps=0, sell_tax_bps=0,
                           sell_ownership_policy="owner_only")
        result = simulate_merged_account(orders, cfg)
        self.assertEqual(result["summary"]["completed_trades"], 1)

    def test_default_config_value_is_any_sell_backward_compatible(self):
        self.assertEqual(MergeConfig().sell_ownership_policy, "any_sell")


if __name__ == "__main__":
    unittest.main()
