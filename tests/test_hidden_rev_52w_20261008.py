"""D15(REVIEW_PLAN §30-1): hidden_rev의 '52주'는 252행 — 이력 1년 미만이면 신호 없음, 1년 이상이면 같은 조건에서 신호."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest_strategies.hidden_rev import _is_buy_hidden_rev  # noqa: E402


def _series(n):
    # 꾸준한 상승 + 마지막 5일 거래량 급증 → 정배열·52주 상단·MA60 위·모멘텀 조건을 만족
    prices = [1000 * (1.004 ** k) for k in range(n)]
    volumes = [100.0] * (n - 5) + [300.0] * 5
    return prices, volumes


class TestHiddenRev52w(unittest.TestCase):
    def _call(self, i, prices, volumes):
        return _is_buy_hidden_rev(i, 0, [""] * len(prices), prices, volumes + [300.0], [], [], [])

    def test_short_history_no_signal(self):
        prices, volumes = _series(201)
        self.assertFalse(self._call(200, prices, volumes))

    def test_full_year_history_can_signal(self):
        prices, volumes = _series(301)
        self.assertTrue(self._call(300, prices, volumes))


if __name__ == "__main__":
    unittest.main()
