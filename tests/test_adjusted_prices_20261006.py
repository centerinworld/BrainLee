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
    c.execute("CREATE TABLE corporate_action_events (stock_code TEXT, event_date TEXT, event_type TEXT, adjustment_status TEXT, backward_price_factor REAL, evidence_rcept_no TEXT, evidence_report_name TEXT)")
    c.executemany("INSERT INTO price_history VALUES ('A',?,?,?,?,?,?)", [(d, p, p, p, p, 1000.0) for d, p in prices])
    c.executemany("INSERT INTO price_jump_audit VALUES (?,?,?,?,0)", audit)
    c.executemany("INSERT INTO corporate_action_events (stock_code,event_date,event_type,adjustment_status,backward_price_factor,evidence_rcept_no) VALUES (?,?,?,?,?,?)",
                  [tuple(e) + (None,) * (6 - len(e)) for e in events])
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


    def test_quarantined_basis_is_not_a_break(self):
        c = _db([(d, 1000.0) for d in DAYS], audit=[("A", DAYS[4], 1.0, "quarantined_basis")])
        e = bc.load_adjusted_prices(c, ["A"], "2024-01-01", "2024-02-01")["A"]
        self.assertEqual(e["breaks"], [])

    def test_limit_exceeding_unexplained_move_is_break_regardless_of_class(self):
        prices = [(d, 1000.0) for d in DAYS[:5]] + [(d, 500.0) for d in DAYS[5:]]   # -50% > 30% 한도, 감사 분류 없음
        c = _db(prices)
        e = bc.load_adjusted_prices(c, ["A"], "2024-01-01", "2024-02-01")["A"]
        self.assertEqual(e["unexplained_limit_breaks"], [DAYS[5]])

    def test_move_within_limit_is_market_move(self):
        prices = [(d, 1000.0) for d in DAYS[:5]] + [(d, 750.0) for d in DAYS[5:]]   # -25% ≤ 30%
        c = _db(prices)
        self.assertEqual(bc.load_adjusted_prices(c, ["A"], "2024-01-01", "2024-02-01")["A"]["breaks"], [])

    def test_gap_rules(self):
        # 공백 ≥60일은 신원 변경 가능 → 무조건 단절, 7~59일은 상태 변경이 있을 때만 단절
        self.assertEqual(bc._limit_breaks(["2024-01-02", "2024-04-15"], [1000.0, 1010.0]), ["2024-04-15"])
        self.assertEqual(bc._limit_breaks(["2024-01-02", "2024-01-20"], [1000.0, 400.0]), [])
        self.assertEqual(bc._limit_breaks(["2024-01-02", "2024-01-20"], [1000.0, 400.0], ("2024-01-10",)), ["2024-01-20"])

    def test_halt_then_limit_move_real_loss_vs_corporate_action(self):
        d = ["2024-01-02", "2024-01-03", "2024-01-04"]
        px, vol = [1000.0, 1000.0, 500.0], [10.0, 0.0, 5.0]       # 정지(거래량 0) 뒤 −50%
        same = lambda day: 1000.0                                  # 상장주식 수 불변 → 실제 손실
        changed = lambda day: 1000.0 if day < "2024-01-04" else 2000.0   # 주식 수 2배 → 기업행위성
        self.assertEqual(bc._limit_breaks(d, px, volumes=vol, shares_at=same), [])
        self.assertEqual(bc._limit_breaks(d, px, volumes=vol, shares_at=changed), ["2024-01-04"])
        # 정지 없이 한도 초과는 주식 수와 무관하게 단절
        self.assertEqual(bc._limit_breaks(d, px, volumes=[10.0, 10.0, 5.0], shares_at=same), ["2024-01-04"])

    def test_halt_resumption_is_not_a_break(self):
        c = _db([("2024-01-02", 1000.0), ("2024-01-15", 400.0), ("2024-01-16", 400.0)])   # 13일 공백 후 재개
        self.assertEqual(bc.load_adjusted_prices(c, ["A"], "2024-01-01", "2024-02-01")["A"]["breaks"], [])

    def test_confirmed_factor_explains_limit_move(self):
        prices = [(d, 1000.0) for d in DAYS[:5]] + [(d, 500.0) for d in DAYS[5:]]
        c = _db(prices, audit=[("A", DAYS[5], 0.5, "confirmed_corporate_action")],
                events=[("A", DAYS[5], "bonus_issue", "factor_confirmed", 0.5)])
        e = bc.load_adjusted_prices(c, ["A"], "2024-01-01", "2024-02-01")["A"]
        self.assertEqual(e["breaks"], [])

    def test_limit_up_day_is_not_a_break_and_next_day_is_free(self):
        # 상한가(+29%, 제한폭 이내) — 감사 분류가 '외부 확인 급변'이어도 단절·제외 구간이 아니다
        prices = [(d, 1000.0) for d in DAYS[:5]] + [(d, 1290.0) for d in DAYS[5:]]
        c = _db(prices, audit=[("A", DAYS[5], 1.29, "externally_confirmed_price_jump_review")])
        e = bc.load_adjusted_prices(c, ["A"], "2024-01-01", "2024-02-01")["A"]
        self.assertEqual(e["breaks"], [])
        self.assertFalse(bc.is_excluded_day(e, DAYS[6]))

    def test_last_tradable_day_skips_halt_and_requires_prior_disclosure(self):
        days = [("2024-01-%02d" % d) for d in range(2, 12)]
        # 01-02~01-05 거래, 01-08~01-10 정지(거래량 0), 01-11 단절(재개)
        vols = [10, 10, 10, 10, 0, 0, 0, 0, 0, 10]
        prices = [(d, 1000.0) for d in days[:9]] + [(days[9], 500.0)]
        c = _db(prices)
        c.execute("DELETE FROM price_history"); c.executemany("INSERT INTO price_history VALUES ('A',?,?,?,?,?,?)", [(d, p, p, p, p, v) for (d, p), v in zip(prices, vols)])
        e = bc.load_adjusted_prices(c, ["A"], "2024-01-01", "2024-02-01")["A"]
        self.assertEqual(bc.last_tradable_day_before_break(e, "2024-01-03", days[9]), days[3])     # 공시 후, 정지 전 마지막 거래일
        self.assertIsNone(bc.last_tradable_day_before_break(e, "2024-01-09", days[9]))            # 정지 후 공시 → 팔 수 없음(평가 불가)
        self.assertIsNone(bc.last_tradable_day_before_break(e, None, days[9]))                    # 공시 근거 없음

    def test_last_day_before_break(self):
        prices = [(d, 1000.0) for d in DAYS[:5]] + [(d, 500.0) for d in DAYS[5:]]
        e = bc.load_adjusted_prices(_db(prices), ["A"], "2024-01-01", "2024-02-01")["A"]
        self.assertEqual(bc.last_day_before_break(e, DAYS[2], DAYS[7]), DAYS[4])
        self.assertIsNone(bc.last_day_before_break(e, DAYS[5], DAYS[7]))   # 이미 단절 뒤에 진입


if __name__ == "__main__":
    unittest.main()


class TestGenericEngineIntegration(unittest.TestCase):
    """W2: 공용 일반 엔진 — 이중 보정 금지·조정 단위 수량 환산 규칙."""

    def test_engine_has_no_legacy_corp_action_patches(self):
        import inspect
        src = inspect.getsource(bc._run_generic_backtest)
        self.assertNotIn("_rebase_positions_for_corp_actions", src)
        self.assertNotIn("_corp_action_adjusted_entry", src)
        self.assertIn("load_adjusted_prices", src)

    def test_default_is_off(self):
        self.assertFalse(bc.ADJUSTED_PRICES_DEFAULT)

    def test_cost_is_preserved_in_adjusted_units(self):
        # 1:1 무상증자(계수 0.5) 이전 진입: 원주가 1000, 조정가 500 → 원주 100주 = 조정 단위 200주, 원가 동일
        f, raw_px, adj_px, budget = 0.5, 1000.0, 500.0, 100_500.0
        qty_raw = int(budget // raw_px)
        qty_adj = qty_raw / f
        self.assertEqual(qty_raw, 100)
        self.assertAlmostEqual(qty_adj * adj_px, qty_raw * raw_px)
        # 사건 뒤 조정가 600(=원주가 600, 사건 전 대비 +20%) → 경제적 손익 +20%
        self.assertAlmostEqual(qty_adj * 600.0 / (qty_raw * raw_px) - 1, 0.2)
