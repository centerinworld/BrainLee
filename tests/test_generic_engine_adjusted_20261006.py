"""(2026-10-07 §10-2) 사전 가격 필터를 끄지 않은 실제 경로에서 시험한다 — 조정 모드는 종목을 통째로 빼지 않는다.
W2 강제 테스트(REVIEW_PLAN §9-2 #1): 실제 DB 위에서 공용 일반 엔진을 돌려 단절·제외 로직이 작동하는지 확인한다.
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
         mock.patch.object(bc, "_record_run_spec", lambda *a, **k: None):
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
        # 077500은 2023-09-26~10-20 거래정지(거래량 0) — 청산은 정지 전 마지막 거래 가능일(09-25)이어야 한다(§18-2)
        self.assertEqual(t["exit_date"], "2023-09-25", t)
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


@unittest.skipUnless(_HAS_DB, "PostgreSQL 접속 불가")
class TestQualityDayBlocks(unittest.TestCase):
    """REVIEW_PLAN §11-2: 1년 차단은 실제 단절에만. 품질 표시는 그날 하루, 제한폭 이내 급등락은 차단하지 않는다."""

    def test_quarantined_day_blocks_only_that_day(self):
        # 054180 2024-12-20 quarantined_basis(비율 1.0): 그날 신호는 빠지고 다음 거래일 신호는 매수로 이어진다
        trades, summary = _run("054180", {"2024-12-20", "2024-12-23"}, "2024-11-01", "2025-03-31")
        self.assertRegex(summary, r'"candidate_skips_quality_day": [1-9]')
        self.assertEqual(len(trades), 1, trades)
        self.assertGreater(trades[0]["entry_date"], "2024-12-23")                    # 12-23 신호 → 다음 거래일 체결
        self.assertNotRegex(summary, r'"candidate_skips_excluded": [1-9]')           # 1년 제외 구간은 만들어지지 않았다

    def test_within_limit_confirmed_jump_class_is_not_blocking(self):
        # 제한폭 이내의 '외부 확인 급변'은 실제 시장 움직임 — 품질 하루 차단 목록에도 1년 제외에도 없다
        self.assertNotIn("externally_confirmed_price_jump_review", bc.QUALITY_DAY_CLASSES)
        self.assertNotIn("externally_confirmed_price_jump_review", bc.UNRESOLVED_BREAK_CLASSES)
        self.assertNotIn("quarantined_basis", bc.UNRESOLVED_BREAK_CLASSES)
