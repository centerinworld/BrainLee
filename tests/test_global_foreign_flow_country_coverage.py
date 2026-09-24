import unittest

from collectors.tic_bilateral_flow_collector import TIC_SERIES
from routes.global_foreign_flow import COUNTRIES, TIC_COUNTRIES, _month_window


class GlobalForeignFlowCountryCoverageTests(unittest.TestCase):
    def test_primary_market_scope_has_exactly_twenty_unique_countries(self):
        codes = [row["code"] for row in TIC_COUNTRIES]
        self.assertEqual(len(codes), 20)
        self.assertEqual(len(set(codes)), 20)
        self.assertNotIn("US", codes)

    def test_every_primary_country_has_a_collector_series(self):
        collected = {our_code for _fred_id, our_code, _label in TIC_SERIES}
        missing = [row["indicator"] for row in TIC_COUNTRIES if row["indicator"] not in collected]
        self.assertFalse(missing, missing)

    def test_fred_series_ids_and_internal_codes_are_unique(self):
        fred_ids = [row[0] for row in TIC_SERIES]
        internal_codes = [row[1] for row in TIC_SERIES]
        self.assertEqual(len(fred_ids), len(set(fred_ids)))
        self.assertEqual(len(internal_codes), len(set(internal_codes)))

    def test_hong_kong_is_present_in_both_distinct_flow_scopes(self):
        self.assertIn("HK", {row["code"] for row in COUNTRIES})
        self.assertIn("HK", {row["code"] for row in TIC_COUNTRIES})

    def test_common_month_window_crosses_year_boundary(self):
        self.assertEqual(_month_window("2026-01", 3), ["2025-11", "2025-12", "2026-01"])


if __name__ == "__main__":
    unittest.main()
