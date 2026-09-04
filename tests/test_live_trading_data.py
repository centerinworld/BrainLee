from __future__ import annotations

from datetime import datetime
import unittest
from unittest.mock import patch

import live_trading_data


class RowCursor:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, results):
        self.results = iter(results)

    def execute(self, _sql, _params=()):
        return RowCursor(**next(self.results))


class LiveTradingDataTest(unittest.TestCase):
    @patch.object(live_trading_data, "ensure_live_data_schema", lambda: None)
    def test_buy_fails_closed_when_required_evidence_is_missing(self):
        conn = FakeConnection([
            {"row": None},
            {"row": None},
            {"row": None},
            {"rows": []},
            {"row": (1,)},
            {"row": (1,)},
            {"row": (1, 1)},
        ])
        result = live_trading_data.evaluate_live_data_contract(
            "005930", "buy", "strategy-a", 1_000_000, now=datetime(2026, 8, 13, 10), conn=conn
        )
        self.assertFalse(result["allowed"])
        self.assertEqual(set(result["reasons"]), {
            "strategy_approval", "tradability", "orderbook", "official_liquidity",
            "intraday_turnover", "corporate_actions", "dilution_completeness",
        })

    @patch.object(live_trading_data, "ensure_live_data_schema", lambda: None)
    def test_sell_is_not_blocked_by_buy_entry_data(self):
        result = live_trading_data.evaluate_live_data_contract(
            "005930", "sell", None, 1_000_000, conn=FakeConnection([])
        )
        self.assertTrue(result["allowed"])
        self.assertEqual(result["decision"], "SELL_RISK_REDUCTION_ALLOWED")


if __name__ == "__main__":
    unittest.main()
