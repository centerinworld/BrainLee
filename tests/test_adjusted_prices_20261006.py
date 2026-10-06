"""W1 (docs/REVIEW_PLAN_20261006.md §1-4): 조정 가격 공용 로더 — 합성 종목 검증."""
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import backtest_common as bc  # noqa: E402


def _db(prices, audit=(), events=()):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE price_history (stock_code TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL)")
    c.execute("CREATE TABLE price_jump_audit (stock_code TEXT, event_date TEXT, price_ratio REAL, classification TEXT, return_usable INT)")
    c.execute("CREATE TABLE corporate_action_events (stock_code TEXT, event_date TEXT, event_type TEXT, adjustment_status TEXT, backward_price_factor REAL)")
    c.executemany("INSERT INTO price_history VALUES ('A',?,?,?,?,?,?)", [(d, p, p, p, p, 1000.0) for d, p in prices])
    c.executemany("INSERT INTO price_jump_audit VALUES (?,?,?,?,0)", audit)
    c.executemany("INSERT INTO corporate_action_events VALUES (?,?,?,?,?)", events)
    return c


DAYS = [("2024-01-%02d" % d) for d in range(2, 12)]


class TestAdjustedPrices(unittest.TestCase):
    def test_bonus_issue_removes_fake_drop(self):
        # 1:1 무상증자: 사건일 가격이 정확히 절반 → 조정 후 사건일 수익률 0%
        prices = [(d, 1000.0) for d in DAYS[:5]] + [(d, 500.0) for d in DAYS[5:]]
        c = _db(prices, audit=[("A", DAYS[5], 0.5, "confirmed_corporate_action")],
                events=[("A", DAYS[5], "bonus_issue", "factor_confirmed", 0.5)])
        e = bc.load_adjusted_prices(c, ["A"], "2024-01-01", "2024-02-01")["A"]
        self.assertAlmostEqual(e["close"][5] / e["close"][4] - 1, 0.0, places=9)
        self.assertEqual(e["raw_close"][4], 1000.0)               # 체결 기록용 원주가 보존
        self.assertAlmostEqual(e["volume"][0], 2000.0)             # 거래량은 계수로 나눔
        self.assertEqual(e["excluded_ranges"], [])

    def test_unconfirmed_break_is_excluded_not_adjusted(self):
        prices = [(d, 1000.0) for d in DAYS[:5]] + [(d, 500.0) for d in DAYS[5:]]
        c = _db(prices, audit=[("A", DAYS[5], 0.5, "corporate_action_pending_confirmation")])
        e = bc.load_adjusted_prices(c, ["A"], "2024-01-01", "2024-02-01", window=3)["A"]
        self.assertEqual(e["close"][4], 1000.0)                    # 근거 없는 계수 금지
        self.assertEqual(e["breaks"], [DAYS[5]])
        self.assertEqual(e["excluded_ranges"], [(DAYS[5], DAYS[8])])
        self.assertTrue(bc.is_excluded_day(e, DAYS[6]))
        self.assertFalse(bc.is_excluded_day(e, DAYS[4]))

    def test_company_split_is_a_break(self):
        prices = [(d, 1000.0) for d in DAYS]
        c = _db(prices, events=[("A", DAYS[3], "company_split", "not_price_adjusting", None)])
        e = bc.load_adjusted_prices(c, ["A"], "2024-01-01", "2024-02-01", window=2)["A"]
        self.assertEqual(e["breaks"], [DAYS[3]])

    def test_break_before_window_ignored(self):
        prices = [(d, 1000.0) for d in DAYS]
        c = _db(prices, audit=[("A", "2015-02-17", 0.5, "quarantined_basis")])
        e = bc.load_adjusted_prices(c, ["A"], "2024-01-01", "2024-02-01")["A"]
        self.assertEqual(e["excluded_ranges"], [])


if __name__ == "__main__":
    unittest.main()
