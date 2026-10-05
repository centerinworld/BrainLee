"""P0-5 (docs/Stock_Strategy.md S16): 4분기 단독 실적은 연간 사업보고서 공시 전에 신호에 쓰이면 안 된다."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backtest_common as bc  # noqa: E402


class TestQ4ReleaseDate(unittest.TestCase):
    def tearDown(self):
        bc._DISC_DATES = {}

    def test_q4_fallback_is_annual_legal_deadline(self):
        bc._DISC_DATES = {}
        self.assertEqual(bc._release_date(2023, 4, False, "A"), "2024-03-31")
        self.assertEqual(bc._release_date(2023, 4, True, "A"), "2024-03-31")
        self.assertEqual(bc._release_date(2023, 3, False, "A"), "2023-11-15")

    def test_q4_uses_actual_annual_disclosure_date(self):
        bc._DISC_DATES = {("A", 2023, 4, 1): "2024-03-12"}
        self.assertEqual(bc._release_date(2023, 4, False, "A"), "2024-03-12")
        self.assertEqual(bc._release_date_with_basis(2023, 4, False, "A"), ("2024-03-12", "actual_disclosure"))
        self.assertEqual(bc._release_date(2023, 4, False, "B"), "2024-03-31")

    def test_no_feb15_fallback_left_in_strategy_sql(self):
        """전략 SQL에 4분기 2/15 폴백이 남아 있지 않아야 한다."""
        bad = []
        for p in list((ROOT / "backtest_strategies").glob("*.py")) + [ROOT / "backtest_common.py"]:
            if re.search(r"%d-02-15", p.read_text(encoding="utf-8")):
                bad.append(p.name)
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
