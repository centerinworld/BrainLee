"""W6(REVIEW_PLAN §34-3): 결정 덮어쓰기 — 기계 등급과 다르면 machine_tier를 남기고, 플래그·메모를 붙인다."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import strategy_governance as sg  # noqa: E402


def _periods(vals, status="point_in_time_verified"):
    return {f"p{i}": {"total_return_pct": v, "verification_status": status, "mdd": -10, "sharpe": 1, "pl_ratio": 1}
            for i, v in enumerate(vals)}


class TestW6Decisions(unittest.TestCase):
    def test_v12_kept_paper_core_with_machine_tier(self):
        g = sg.apply_governance_decision("v12", sg.classify_strategy(_periods([38.1, 0, 4.3, 6.5, -4.5, 60.7])))
        self.assertEqual(g["tier"], "paper_core")
        self.assertEqual(g["machine_tier"], "validation_queue")

    def test_v8_flag(self):
        g = sg.apply_governance_decision("v8", sg.classify_strategy(_periods([65.3, 0, 16.4, 14.5, -8.1, 37.4])))
        self.assertEqual(g["tier"], "paper_core")
        self.assertEqual(g["decision_flag"], "forward_validation_pending_60d")
        self.assertNotIn("machine_tier", g)   # 기계 산정도 paper_core

    def test_minervini_retired_and_unknown_strategy_untouched(self):
        self.assertEqual(sg.apply_governance_decision("minervini", sg.classify_strategy(_periods([30] * 6)))["tier"], "retired")
        base = sg.classify_strategy(_periods([1] * 6))
        self.assertEqual(sg.apply_governance_decision("zz_none", base), base)

    def test_sector_note(self):
        g = sg.apply_governance_decision("sector_focus", sg.classify_strategy(_periods([34] * 6, "point_in_time_approx")))
        self.assertEqual(g["tier"], "offensive_satellite")
        self.assertIn("생존 편향", g["decision_note"])


if __name__ == "__main__":
    unittest.main()
