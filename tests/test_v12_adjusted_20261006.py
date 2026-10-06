"""W3(v12) 강제 테스트: 실제 데이터로 v12를 한 번 돌려 실제로 보유한 종목을 고르고, 그 보유 구간 안에 단절을 주입해
(공시 있음/없음) 엔진이 각각 '단절 전 청산'·'단절 당일 청산'을 하는지, 제외 구간 안에서는 진입하지 않는지 확인한다.
운영 DB에 쓰지 않는다(_save_result·_record_run_spec·_register_execution_artifacts 패치). PostgreSQL이 없으면 건너뛴다."""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import backtest_common as bc  # noqa: E402
import backtest_strategies.v12 as v12  # noqa: E402

try:
    from db_compat import connect_primary_db
    _c = connect_primary_db(timeout=10)
    _HAS_DB = bool(_c.execute("SELECT 1 FROM price_history LIMIT 1").fetchone())
    _c.close()
except Exception:  # pragma: no cover
    _HAS_DB = False

START, END = "2024-01-02", "2024-06-28"


def _run(loader=None):
    captured = {}
    patches = [mock.patch.object(v12, "_save_result", lambda run_id, result: captured.update(result)),
               mock.patch.object(v12, "_record_run_spec", lambda *a, **k: None),
               mock.patch.object(v12, "_register_execution_artifacts", lambda *a, **k: None)]
    if loader is not None:
        patches.append(mock.patch.object(v12, "load_adjusted_prices", loader))
    for p in patches:
        p.start()
    try:
        v12.run_backtest_v12(START, END, run_id="v12_engine_test", adjusted_prices=True)
    finally:
        for p in patches:
            p.stop()
    return captured


@unittest.skipUnless(_HAS_DB, "PostgreSQL 접속 불가")
class TestV12Adjusted(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = _run()
        cls.trades = cls.base["trades"]

    def test_runs_and_records_raw_fields(self):
        self.assertGreater(len(self.trades), 0)
        self.assertIn("조정가격(W3)", self.base["summary"])
        self.assertTrue(all("entry_price_raw" in t for t in self.trades if not t["exit_reason"].startswith("기간")))

    def _inject(self, trade, kind):
        """trade 보유 구간 중간 날(break_day)에 단절을 주입하는 로더."""
        orig = bc.load_adjusted_prices
        code, entry, exit_ = trade["stock_code"], trade["entry_date"], trade["exit_date"]

        def loader(conn, codes, start, end, **kw):
            out = orig(conn, codes, start, end, **kw)
            e = out[code]
            ds = e["dates"]
            mid = [d for d in ds if entry < d < exit_]
            break_day = mid[len(mid) // 2]
            e["breaks"] = sorted(set(e["breaks"]) | {break_day})
            e["break_disclosed"] = dict(e.get("break_disclosed") or {})
            prev = [d for d in ds if d < break_day][-1]
            e["break_disclosed"][break_day] = prev if kind == "disclosed" else None
            range_end = ds[min(ds.index(break_day) + 20, len(ds) - 1)]
            e["excluded_ranges"] = list(e["excluded_ranges"]) + [(break_day, range_end)]
            loader.break_day, loader.range_end = break_day, range_end
            return out
        return loader

    def _held_trade(self):
        cands = [t for t in self.trades if t["entry_date"] < t["exit_date"] and
                 len([d for d in (bc_dates(t["stock_code"])) if t["entry_date"] < d < t["exit_date"]]) >= 4]
        self.assertTrue(cands, "시험할 보유 거래가 없다")
        return cands[0]

    def test_disclosed_break_closes_before_event(self):
        t = self._held_trade()
        loader = self._inject(t, "disclosed")
        res = _run(loader)
        mine = [x for x in res["trades"] if x["stock_code"] == t["stock_code"] and x["entry_date"] == t["entry_date"]]
        self.assertEqual(len(mine), 1, mine)
        self.assertTrue(mine[0]["exit_reason"].startswith("단절 전 청산"), mine[0])
        self.assertLess(mine[0]["exit_date"], loader.break_day)

    def test_undisclosed_break_closes_on_break_day(self):
        t = self._held_trade()
        loader = self._inject(t, "undisclosed")
        res = _run(loader)
        mine = [x for x in res["trades"] if x["stock_code"] == t["stock_code"] and x["entry_date"] == t["entry_date"]]
        self.assertEqual(len(mine), 1, mine)
        self.assertTrue(mine[0]["exit_reason"].startswith("단절 당일 청산"), mine[0])
        self.assertEqual(mine[0]["exit_date"], loader.break_day)

    def test_no_entry_inside_injected_excluded_range(self):
        t = self._held_trade()
        loader = self._inject(t, "disclosed")
        res = _run(loader)
        for x in res["trades"]:
            if x["stock_code"] == t["stock_code"]:
                self.assertFalse(loader.break_day <= x["entry_date"] <= loader.range_end, x)


def bc_dates(code):
    c = connect_primary_db(timeout=30)
    try:
        return [r[0] for r in c.execute("SELECT date FROM price_history WHERE stock_code=? AND date>=? AND date<=? AND close>0 ORDER BY date",
                                        (code, START, END)).fetchall()]
    finally:
        c.close()


if __name__ == "__main__":
    unittest.main()
