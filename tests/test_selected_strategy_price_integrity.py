import unittest

from scripts.audit_selected_strategy_price_integrity import holding_windows


class HoldingWindowNormalizationTest(unittest.TestCase):
    def test_action_events_are_paired(self):
        trades = [
            {"code": "005930", "date": "2025-01-02", "action": "BUY"},
            {"code": "005930", "date": "2025-01-10", "action": "SELL"},
        ]
        self.assertEqual(holding_windows(trades, "2025-12-31"), [
            ("005930", "2025-01-02", "2025-01-10"),
        ])

    def test_round_trip_aliases_are_supported(self):
        trades = [
            {"stock_code": "000660", "entry_date": "2025-02-03", "exit_date": "2025-02-20"},
            {"code": "035420", "buy_date": "2025-03-04", "sell_date": "2025-03-21"},
            {"sc": "035720", "entry": "2025-04-01", "exit": "2025-04-15"},
        ]
        self.assertEqual(holding_windows(trades, "2025-12-31"), [
            ("000660", "2025-02-03", "2025-02-20"),
            ("035420", "2025-03-04", "2025-03-21"),
            ("035720", "2025-04-01", "2025-04-15"),
        ])

    def test_open_position_closes_at_period_end(self):
        trades = [{"stock_code": "051910", "buy_date": "2025-05-01", "action": "buy"}]
        self.assertEqual(holding_windows(trades, "2025-06-30"), [
            ("051910", "2025-05-01", "2025-06-30"),
        ])


if __name__ == "__main__":
    unittest.main()
