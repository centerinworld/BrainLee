"""docs/Stock_Strategy.md S27·D13: v12 매수 후보 선택이 테이블 물리 순서에 좌우되지 않는다."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "backtest_strategies" / "v12.py").read_text(encoding="utf-8")


class TestV12SelectionOrder(unittest.TestCase):
    def test_universe_query_is_ordered(self):
        # 종목 반복 순서가 ORDER BY 없는 조회(물리 순서)에 의존하면 같은 데이터로도 결과가 바뀐다.
        start = SRC.index("_sector_universe_sql = ")
        self.assertIn('ORDER BY stock_code', SRC[start:start + 800])

    def test_default_is_score_order(self):
        for fn in ("def run_backtest_v12(", "def _run_backtest_v12("):
            head = SRC[SRC.index(fn):SRC.index(":\n", SRC.index(") ", SRC.index(fn))) if fn.startswith("def run") else SRC.index("):", SRC.index(fn))]
            self.assertIn('selection_order: str = "score"', head, fn)

    def test_candidates_sorted_before_filling_slots(self):
        # 점수 내림차순, 동점은 종목코드 — 반복 순서를 뒤섞어도 같은 결과여야 한다.
        self.assertIn("_v12_cands.sort(key=lambda x: (-x[0], x[1]))", SRC)
        cands = [(1.5, "336570", 0), (3.0, "145720", 0), (1.5, "000100", 0), (-1.0, "005930", 0)]
        expected = None
        for perm in (cands, list(reversed(cands)), cands[2:] + cands[:2]):
            got = [c[1] for c in sorted(perm, key=lambda x: (-x[0], x[1]))]
            expected = expected or got
            self.assertEqual(got, expected)
        self.assertEqual(expected, ["145720", "000100", "336570", "005930"])

    def test_no_first_come_break_in_scan(self):
        # 스캔 루프 안에서 자리 수로 break하면 다시 선착순이 된다(자리 채우기는 정렬 뒤 루프에서만).
        scan = SRC[SRC.index("_v12_cands = []"):SRC.index("if selection_order == \"score\":")]
        self.assertIsNone(re.search(r"len\(positions\) >= max_positions:\s*\n\s*break", scan))


if __name__ == "__main__":
    unittest.main()
