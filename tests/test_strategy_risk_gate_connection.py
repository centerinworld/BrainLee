from __future__ import annotations

import unittest
from unittest.mock import patch

from routes.trend import _paper_buy_gate
from routes.kis_trading import authorize_strategy_order


class StrategyRiskGateConnectionTest(unittest.TestCase):
    @patch("routes.kis_trading.authorize_strategy_order")
    def test_virtual_buy_uses_strict_execution_gate(self, evaluate):
        evaluate.return_value = {"decision": "BUY_ALLOWED", "reasons": []}

        result = _paper_buy_gate("005930", "v_gc", 3, 70_000)

        self.assertEqual(result["decision"], "BUY_ALLOWED")
        evaluate.assert_called_once_with(
            "005930", "buy", 3, 70_000.0, "v_gc",
            decision_source="strategy_virtual_execution",
        )

    def test_invalid_virtual_order_is_blocked_before_gate(self):
        result = _paper_buy_gate("", "v_gc", 0, 0)
        self.assertEqual(result["decision"], "BLOCKED_RISK")

    def test_universal_gateway_rejects_missing_strategy_identity(self):
        result = authorize_strategy_order(
            "005930", "buy", 1, 70_000, None, decision_source="test"
        )
        self.assertEqual(result["decision"], "BLOCKED_RISK")

    @patch("routes.kis_trading.evaluate_risk_gates")
    def test_universal_gateway_accepts_any_named_strategy(self, evaluate):
        evaluate.return_value = {"decision": "BUY_ALLOWED", "reasons": []}
        authorize_strategy_order(
            "005930", "buy", 1, 70_000, "future_strategy_27",
            decision_source="test",
        )
        evaluate.assert_called_once_with(
            "005930", "buy", 1, 70_000, "future_strategy_27",
            strict_for_execution=True, decision_source="test",
        )


if __name__ == "__main__":
    unittest.main()
