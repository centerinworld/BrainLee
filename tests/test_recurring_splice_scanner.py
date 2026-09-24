import unittest

from scripts.scan_recurring_splice_glitches_20260912 import overlaps_corporate_action_window


class CorporateActionWindowTest(unittest.TestCase):
    def test_event_inside_three_day_margin_is_excluded(self):
        self.assertTrue(overlaps_corporate_action_window(
            ["2024-01-15"], {"2024-01-12"}, days=3,
        ))

    def test_event_outside_three_day_margin_is_kept(self):
        self.assertFalse(overlaps_corporate_action_window(
            ["2024-01-15"], {"2024-01-11"}, days=3,
        ))

    def test_margin_applies_to_both_episode_edges(self):
        self.assertTrue(overlaps_corporate_action_window(
            ["2020-04-10", "2020-04-23"], {"2020-04-24"}, days=3,
        ))


if __name__ == "__main__":
    unittest.main()
