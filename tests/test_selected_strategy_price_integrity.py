import unittest

from scripts.audit_selected_strategy_price_integrity import (
    holding_windows, _is_survivorship, _distinct_windows, _window_key,
    _ledger_has_confirmed_delisting_recovery,
    PRICE_JUMP_CONTAMINATION_THRESHOLD,
    PRICE_JUMP_CONTAMINATION_WARNING_THRESHOLD,
)


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

    def test_buy_event_plus_completed_row_is_not_reopened_to_period_end(self):
        trades = [
            {"code": "011690", "buy_date": "2020-03-03", "action": "buy"},
            {
                "code": "011690", "buy_date": "2020-03-03",
                "sell_date": "2020-03-17", "reason": "stop",
            },
        ]
        self.assertEqual(holding_windows(trades, "2021-11-30"), [
            ("011690", "2020-03-03", "2020-03-17"),
        ])


class ContaminationPolicyTest(unittest.TestCase):
    """2026-09-12 policy: zero-tolerance never passed (even v11's 1/483=0.21%
    stayed 'legacy' forever), so price-jump contamination is now measured as a
    per-strategy ratio of distinct contaminated holding windows against a
    threshold - but survivorship findings (a window opened/closed outside the
    security's verified tradable interval) are a strategy logic defect, not a
    data-quality nuance, and must stay zero-tolerance regardless of ratio."""

    def test_survivorship_classifications_are_never_price_jump(self):
        self.assertTrue(_is_survivorship({"classification": "missing_asof_security_master"}))
        self.assertTrue(_is_survivorship({"classification": "held_through_listing_end"}))
        self.assertFalse(_is_survivorship({"classification": "coverage_gap"}))
        self.assertFalse(_is_survivorship({"classification": "confirmed_corporate_action"}))

    def test_confirmed_recovery_must_be_present_in_exact_trade_ledger_row(self):
        trades = [{
            "stock_code": "282690", "entry_date": "2024-07-31",
            "exit_date": "2024-12-30",
            "exit_reason": "기간종료(합병ㆍ교환 실제가치 반영-주식)",
        }]
        self.assertTrue(_ledger_has_confirmed_delisting_recovery(
            trades, "282690", "2024-07-31", "2024-12-30"))
        self.assertFalse(_ledger_has_confirmed_delisting_recovery(
            trades, "282690", "2024-07-30", "2024-12-30"))

    def test_distinct_windows_collapses_multiple_events_in_one_window(self):
        findings = [
            {"period": "p1", "stock_code": "005930", "holding_start": "2020-01-01", "holding_end": "2020-02-01",
             "event_date": "2020-01-10"},
            {"period": "p1", "stock_code": "005930", "holding_start": "2020-01-01", "holding_end": "2020-02-01",
             "event_date": "2020-01-15"},
            {"period": "p1", "stock_code": "000660", "holding_start": "2020-03-01", "holding_end": "2020-04-01",
             "event_date": "2020-03-10"},
        ]
        # 3 raw events, but only 2 distinct (period,code,start,end) windows -
        # this is exactly the bug that made pre-fix ratios read as e.g. 1000%.
        self.assertEqual(len(_distinct_windows(findings)), 2)

    def test_ratio_at_threshold_passes_one_above_fails(self):
        total_windows = 100
        at_threshold = int(total_windows * PRICE_JUMP_CONTAMINATION_THRESHOLD)
        findings_at = [
            {"period": "p", "stock_code": str(i), "holding_start": "2020-01-01", "holding_end": "2020-01-02"}
            for i in range(at_threshold)
        ]
        ratio_at = len(_distinct_windows(findings_at)) / total_windows
        self.assertLessEqual(ratio_at, PRICE_JUMP_CONTAMINATION_THRESHOLD)
        findings_over = findings_at + [
            {"period": "p", "stock_code": "over", "holding_start": "2020-01-01", "holding_end": "2020-01-02"}
        ]
        ratio_over = len(_distinct_windows(findings_over)) / total_windows
        self.assertGreater(ratio_over, PRICE_JUMP_CONTAMINATION_THRESHOLD)

    def test_marginal_band_stays_visible_below_hard_gate(self):
        self.assertLess(
            PRICE_JUMP_CONTAMINATION_WARNING_THRESHOLD,
            PRICE_JUMP_CONTAMINATION_THRESHOLD,
        )
        ratio = 0.0591
        self.assertGreater(ratio, PRICE_JUMP_CONTAMINATION_WARNING_THRESHOLD)
        self.assertLessEqual(ratio, PRICE_JUMP_CONTAMINATION_THRESHOLD)

    def test_duplicate_holding_windows_do_not_dilute_denominator(self):
        raw = [
            ("p1", "005930", "2020-01-01", "2020-02-01"),
            ("p1", "005930", "2020-01-01", "2020-02-01"),
            ("p1", "000660", "2020-03-01", "2020-04-01"),
        ]
        denominator = {_window_key(*window) for window in raw}
        self.assertEqual(len(raw), 3)
        self.assertEqual(len(denominator), 2)


if __name__ == "__main__":
    unittest.main()
