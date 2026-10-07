"""REVIEW_PLAN §23 강제 테스트(2026-10-07).

① 연구 경로는 확정 가격만: 잠정 표시 행이 있는 날짜를 end로 주면 그 날짜는 쓰지 않는다(§23-2 ②).
② 스탁이지 미러 가상계좌(…_mirror)는 어떤 설정으로도 실주문 불가(§23-3 필수 안전장치).
③ 미러 계좌는 노출 합산·공통 손절 대상에서 빠진다(쿼리 문자열 확인 — DB 쓰기 없음).
PostgreSQL 필요 항목은 실제 표에 트랜잭션 안에서만 쓰고 항상 롤백한다.
"""
import importlib
import inspect
import os
import unittest
from unittest import mock

from config import IS_POSTGRES


@unittest.skipUnless(IS_POSTGRES, "Postgres only")
class ConfirmedEndDateTest(unittest.TestCase):
    def test_provisional_date_excluded_from_end(self):
        from backtest_common import confirmed_end_date
        from db_compat import connect_primary_db
        conn = connect_primary_db(timeout=60)
        try:
            conn.execute("INSERT INTO price_provisional_rows(stock_code,date,source,close,written_at) "
                         "VALUES ('999990','2001-01-10','test',1,'t') ON CONFLICT DO NOTHING")
            self.assertEqual(confirmed_end_date("2001-01-10", conn), "2001-01-09")
            self.assertEqual(confirmed_end_date("20010131", conn), "20010109")
            self.assertEqual(confirmed_end_date("2001-01-05", conn), "2001-01-05")
        finally:
            conn._connection.rollback()
            conn.close()


class MirrorNeverLiveTest(unittest.TestCase):
    def test_mirror_key_in_env_approval_is_ignored(self):
        with mock.patch.dict(os.environ, {"STOCKEASY_LIVE_APPROVED_STRATEGIES": "peak,peak_mirror,momentum_mirror"}):
            import stockeasy_autotrade
            mod = importlib.reload(stockeasy_autotrade)
            self.assertIn("peak", mod.LIVE_APPROVED_STRATEGIES)
            self.assertNotIn("peak_mirror", mod.LIVE_APPROVED_STRATEGIES)
            self.assertNotIn("momentum_mirror", mod.LIVE_APPROVED_STRATEGIES)
            src = inspect.getsource(mod)
            self.assertIn('and not str(strategy).endswith("_mirror")', src)
        importlib.reload(stockeasy_autotrade)

    def test_authorize_strategy_order_blocks_mirror(self):
        from routes.kis_trading import authorize_strategy_order
        r = authorize_strategy_order("005930", "buy", 1, 1000.0, "peak_mirror", decision_source="test")
        self.assertEqual(r["decision"], "BLOCKED_RISK")
        self.assertIn("paper-only", r["reasons"][0])


class MirrorExcludedFromGuardsTest(unittest.TestCase):
    def test_guard_and_hardstop_queries_exclude_mirror(self):
        import virtual_trade_guards
        from routes import trend
        mirror_list = "'peak_mirror','momentum_mirror','value_mirror'"
        g = inspect.getsource(virtual_trade_guards.check_entry)
        self.assertGreaterEqual(g.count(mirror_list), 3)  # 종목당 전략 수·섹터 비중·재진입 쿨다운
        h = inspect.getsource(trend._auto_hardstop_all_strategies)
        self.assertIn(mirror_list, h)
        self.assertEqual(trend.MIRROR_STRATEGIES, {"peak_mirror", "momentum_mirror", "value_mirror"})
        b = inspect.getsource(trend.trend_buy)
        self.assertIn('bool(payload.get("mirror")) and strategy in MIRROR_STRATEGIES', b)


if __name__ == "__main__":
    unittest.main()
