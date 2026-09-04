import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ETF_check"))

from etf_membership_coverage import classify  # noqa: E402


class CoverageClassificationTest(unittest.TestCase):
    def test_partial_top_n_is_not_complete(self):
        status, ratio = classify(expected=200, returned=30, domestic=30)
        self.assertEqual(status, "partial")
        self.assertAlmostEqual(ratio, 0.15)

    def test_unknown_empty_is_not_confirmed_empty(self):
        status, ratio = classify(expected=None, returned=0, domestic=0)
        self.assertEqual(status, "unknown_empty")
        self.assertIsNone(ratio)

    def test_explicit_zero_is_complete_empty(self):
        status, ratio = classify(expected=0, returned=0, domestic=0)
        self.assertEqual(status, "complete_empty")
        self.assertEqual(ratio, 1.0)

    def test_full_response_is_complete(self):
        status, ratio = classify(expected=17, returned=17, domestic=17)
        self.assertEqual(status, "complete")
        self.assertEqual(ratio, 1.0)


if __name__ == "__main__":
    unittest.main()
