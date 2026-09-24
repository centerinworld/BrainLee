import unittest

from collectors.ecb_euro_equity_flow_collector import parse_ecb_csv


class EcbEuroEquityFlowCollectorTests(unittest.TestCase):
    def test_parses_monthly_observations_and_skips_invalid_values(self):
        payload = "TIME_PERIOD,OBS_VALUE\n2026-01,63288.7272\n2026-02,\n2026-03,-120.5\n"
        self.assertEqual(
            parse_ecb_csv(payload),
            [("2026-01-01", 63288.7272), ("2026-03-01", -120.5)],
        )


if __name__ == "__main__":
    unittest.main()
