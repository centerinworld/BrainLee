"""W2 강제 테스트(REVIEW_PLAN §9-2 #1): 실제 DB 위에서 공용 일반 엔진을 돌려 단절·제외 로직이 작동하는지 확인한다.
신호 함수는 코드 인자가 없으므로, 대상 종목의 조정 종가가 정확히 일치하는 날만 매수 신호를 낸다(같은 로더가 만든 값이라 일치).
운영 DB에는 아무것도 쓰지 않는다(_save_result·_record_run_spec 패치). PostgreSQL에 접속할 수 없으면 건너뛴다."""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import backtest_common as bc  # noqa: E402

try:
    from db_compat import connect_primary_db
    _c = connect_primary_db(timeout=10)
    _HAS_DB = bool(_c.execute("SELECT 1 FROM price_history LIMIT 1").fetchone())
    _c.close()
except Exception:  # pragma: no cover
    _HAS_DB = False


def _run(code, signal_dates, start, end):
    """code 종목이 signal_dates에 매수 신호를 내는 엔진 실행 → (거래 목록, 통계 요약)."""
    conn = connect_primary_db(timeout=60)
    warm = bc.datetime.strptime(start, "%Y-%m-%d") - bc.timedelta(days=450)
    ap = bc.load_adjusted_prices(conn, [code], warm.strftime("%Y-%m-%d"), end)[code]
    conn.close()
    target = {d: c for d, c in zip(ap["dates"], ap["close"]) if d in signal_dates}

    def sig(i, sim_start_i, dates, prices, volumes, frn, inst, fins):
        return i >= sim_start_i and dates[i] in target and abs(prices[i] - target[dates[i]]) < 1e-9

    captured = {}
    with mock.patch.object(bc, "_save_result", lambda run_id, result: captured.update(result)), \
         mock.patch.object(bc, "_record_run_spec", lambda *a, **k: None), \
         mock.patch.object(bc, "assert_research_prices", lambda *a, **k: set()):   # 엔진의 기존 가격 사전 필터(미확인 사건 종목 통째 제외)를 끈다 — 로더 로직만 시험
        bc._run_generic_backtest(
            "T", sig, start, end, per_stock=10_000_000, max_positions=10, run_name="engine-test", run_id="engine_test_x",
            stop_loss=-0.99, take_profit=50.0, trail_stop=-0.99, mktcap_min=0, use_market_filter=False,
            asof_mktcap=False, adjusted_prices=True)
    return [t for t in captured["trades"] if t["stock_code"] == code], captured["summary"]


@unittest.skipUnless(_HAS_DB, "PostgreSQL 접속 불가")
class TestEngineBreakHandling(unittest.TestCase):
    def test_pre_break_liquidation_after_disclosure(self):
        # 077500 회사분할 2023-10-23(공시 접수 2023-10-20): 2023-09-15 진입 → 단절 전 거래일 종가 청산 1건
        trades, summary = _run("077500", {"2023-09-14"}, "2023-08-01", "2023-12-28")
        self.assertEqual(len(trades), 1, trades)
        t = trades[0]
        self.assertEqual(t["exit_reason"].split(" ")[0], "단절", t)
        self.assertLess(t["exit_date"], "2023-10-23")
        self.assertGreater(t["profit_pct"], -30.0)   # 가짜 −64% 손실이 아니다

    def test_no_entry_inside_excluded_range(self):
        # 같은 종목 단절 뒤(제외 구간) 신호 → 매수 0건, 건너뜀 통계 ≥ 1
        trades, summary = _run("077500", {"2023-11-02"}, "2023-10-01", "2024-03-29")
        self.assertEqual(trades, [])
        self.assertRegex(summary, r'"candidate_skips_excluded": [1-9]')

    def test_bonus_issue_hold_is_not_stopped_out(self):
        # 196170 무상증자(2020-07-23, 계수 0.5, 원주가 −40%): 보유 중이어도 가짜 손절·단절 청산이 없다
        trades, summary = _run("196170", {"2020-07-20"}, "2020-06-01", "2020-12-30")
        self.assertEqual(len(trades), 1, trades)
        t = trades[0]
        self.assertNotIn("단절", t["exit_reason"])
        self.assertNotIn("손절", t["exit_reason"])
        self.assertGreater(t["profit_pct"], -35.0)
        self.assertIsNotNone(t.get("entry_price_raw"))


if __name__ == "__main__":
    unittest.main()
