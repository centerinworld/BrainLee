"""REVIEW_PLAN §23 강제 테스트(2026-10-07).

① 연구 경로는 확정 가격만: 그날 대부분이 잠정인 날짜는 end에서 자르고, 남은 개별 잠정 행은 그 행만 뺀다(§23-2 ②·§25-2 ①④).
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
    """§25-2 ①: 날짜 컷은 그날 대부분이 잠정인 날만, 남은 개별 잠정 행은 그 행만 결측."""

    def setUp(self):
        from db_compat import connect_primary_db
        self.conn = connect_primary_db(timeout=60)
        c = self.conn
        c.execute("SELECT set_config('app.price_basis_checked','1', true)")
        c.execute("DELETE FROM price_provisional_rows")   # 실제 표시 영향 배제 — 트랜잭션 안, 끝에서 롤백
        # 그날 전부 잠정인 날(가짜 미래 날짜): 날짜 컷 대상
        c.execute("INSERT INTO price_history(stock_code,date,open,high,low,close,volume) VALUES ('999990','2099-01-02',1,1,1,1,1)")
        c.execute("INSERT INTO price_provisional_rows(stock_code,date,source,close,written_at) "
                  "VALUES ('999990','2099-01-02','test',1,'t') ON CONFLICT DO NOTHING")
        # 과거 날짜 잠정 1행(그날 수천 행 중 1행): 날짜 컷 아님, 그 행만 제외
        c.execute("INSERT INTO price_provisional_rows(stock_code,date,source,close,written_at) "
                  "VALUES ('005930','2026-09-01','test',1,'t') ON CONFLICT DO NOTHING")

    def tearDown(self):
        self.conn._connection.rollback()
        self.conn.close()

    def test_mostly_provisional_date_cuts_end(self):
        from backtest_common import confirmed_end_date
        self.assertEqual(confirmed_end_date("2099-01-05", self.conn), "2099-01-01")
        self.assertEqual(confirmed_end_date("20990131", self.conn), "20990101")

    def test_single_leftover_row_does_not_cut_date(self):
        from backtest_common import confirmed_end_date
        self.assertEqual(confirmed_end_date("2026-09-30", self.conn), "2026-09-30")
        self.assertEqual(confirmed_end_date("2026-09-01", self.conn), "2026-09-01")

    def test_single_leftover_row_excluded_from_loader(self):
        from backtest_common import load_adjusted_prices, provisional_rows
        self.assertIn(("005930", "2026-09-01"), provisional_rows(self.conn, "2026-08-25", "2026-09-05"))
        d = load_adjusted_prices(self.conn, ["005930"], "2026-08-25", "2026-09-05")["005930"]["dates"]
        self.assertNotIn("2026-09-01", d)
        self.assertIn("2026-09-02", d)
        self.assertIn("2026-08-29", d) if "2026-08-29" in d else self.assertIn("2026-08-28", d)
        self.assertEqual(len(provisional_rows(self.conn, "2026-08-25", "2026-09-05")), 1)


class ConfirmedEndEntryPointTest(unittest.TestCase):
    """§25-2 ④: `backtest`가 재내보내는 전략 실행 함수 전부가 확정 가격 end를 쓴다."""

    def test_all_strategy_runners_wrapped(self):
        import backtest
        names = [n for n in dir(backtest) if n.startswith("run_backtest")]
        self.assertGreaterEqual(len(names), 40)
        self.assertEqual([n for n in names if not getattr(getattr(backtest, n), "__confirmed_end__", False)], [])

    def test_wrapper_passes_cut_end_and_records_requested(self):
        import backtest_common as bc
        seen = {}
        with mock.patch.object(bc, "confirmed_end_date", lambda e, conn=None: "2026-10-05"), \
                mock.patch.object(bc, "record_requested_end", lambda rid, req, actual=None: seen.update(rid=rid, req=req, actual=actual)):
            fn = bc.with_confirmed_end(lambda start_date, end_date, run_id=None: (seen.update(end=end_date), "r1")[1])
            self.assertEqual(fn("2026-01-01", "2026-10-07"), "r1")
        self.assertEqual(seen, {"end": "2026-10-05", "rid": "r1", "req": "2026-10-07", "actual": "2026-10-05"})

    def test_new_rerun_scripts_go_through_backtest_module(self):
        import re
        from pathlib import Path
        root = Path(__file__).resolve().parents[1] / "scripts"
        bad = []
        for f in root.glob("rerun_*.py"):
            m = re.search(r"_(\d{8})\.py$", f.name)
            if not m or m.group(1) < "20261008":
                continue   # 기존(이미 실행 끝난) 스크립트는 그대로
            src = f.read_text()
            if "import backtest_strategies" in src or "from backtest_strategies" in src:
                if "confirmed_end_date" not in src:
                    bad.append(f.name)
        self.assertEqual(bad, [], "새 재실행 스크립트는 import backtest 경유 또는 confirmed_end_date 적용 필수(REVIEW_PLAN §25-2 ④)")

    def test_routes_record_requested_end(self):
        from routes import backtest as rb
        src = inspect.getsource(rb)
        self.assertNotIn("_bt.confirmed_end_date(payload", src)
        self.assertEqual(src.count("_end(payload,"), src.count("return _started(run_id, payload)"))


class MirrorExcludedFromResearchTest(unittest.TestCase):
    def test_adoption_inputs_exclude_mirror(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "research" / "extract_adoption_inputs_20260925.py").read_text()
        self.assertIn("strategy NOT IN ('peak_mirror','momentum_mirror','value_mirror')", src)


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
