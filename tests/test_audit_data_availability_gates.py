import unittest

from scripts.audit_selected_strategy_data_availability import (
    _VERIFIED_OPTIONAL_DELAYED_DATA_GATES,
    _delayed_data_gate_off,
)


class DelayedDataGateOffTest(unittest.TestCase):
    def test_unlisted_strategy_is_never_exempted(self):
        # No manually-verified gate mapping exists for this strategy, so the
        # crude whole-file text scan's result must stand unmodified.
        self.assertFalse(_delayed_data_gate_off("golden_cross", {}))

    def test_megatrend_gate_off_when_param_missing(self):
        # The registered run's parameter_json doesn't even have the key -
        # meaning the function's own default (False) was used at run time.
        self.assertTrue(_delayed_data_gate_off("megatrend", {}))

    def test_megatrend_gate_off_when_explicitly_false(self):
        self.assertTrue(
            _delayed_data_gate_off("megatrend", {"require_earnings_accel": False})
        )

    def test_megatrend_gate_on_is_not_exempted(self):
        # If a future run actually turns the flag on, it must NOT be waved
        # through - the strategy genuinely reads financial_data in that case.
        self.assertFalse(
            _delayed_data_gate_off("megatrend", {"require_earnings_accel": True})
        )

    def test_mapping_is_curated_not_guessed(self):
        # Documents the exact, source-verified scope of this exemption so a
        # future edit can't silently widen it without re-reading the code.
        self.assertEqual(
            _VERIFIED_OPTIONAL_DELAYED_DATA_GATES,
            {"megatrend": {"require_earnings_accel": False}},
        )


if __name__ == "__main__":
    unittest.main()
