"""W3(golden_cross) 강제 테스트 — v12 테스트와 같은 방식: 실제 데이터로 한 번 돌려 보유한 거래를 고르고, 그 보유 구간에 단절을 주입한다.
golden_cross는 결과를 SQL로 직접 UPDATE하므로 테스트가 임시 backtest_runs 행을 만들고 읽은 뒤 지운다(_record_run_spec 패치로 spec은 남기지 않음)."""
import json
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import backtest_common as bc  # noqa: E402
import backtest_strategies.golden_cross as gc  # noqa: E402

try:
    from db_compat import connect_primary_db
    _c = connect_primary_db(timeout=10)
    _HAS_DB = bool(_c.execute("SELECT 1 FROM price_history LIMIT 1").fetchone())
    _c.close()
except Exception:  # pragma: no cover
    _HAS_DB = False

START, END = "2024-01-02", "2024-09-30"


class _Spy:
    """gc가 결과를 `UPDATE backtest_runs SET status='done'`으로 저장하는 문장을 가로채 DB에 쓰지 않고 파라미터만 잡는다."""
    def __init__(self, real, sink):
        self._real, self._sink = real, sink

    def execute(self, sql, params=()):
        if "status='done'" in sql and "trades_json" in sql:
            self._sink["params"] = params
            return None
        return self._real.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _run(loader=None):
    rid = "gc_test_" + uuid.uuid4().hex[:6]
    sink = {}
    real_connect = gc.sqlite3.connect

    def connect(*a, **k):
        return _Spy(real_connect(*a, **k), sink)

    c = connect_primary_db(timeout=60)
    c.execute("INSERT INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,status) VALUES (?,?,?,?,?,?,'running')",
              (rid, "gc-engine-test", "golden_cross", START, END, 10_000_000))
    c.commit(); c.close()
    patches = [mock.patch.object(gc, "_record_run_spec", lambda *a, **k: None),
               mock.patch.object(gc, "_register_execution_artifacts", lambda *a, **k: None),
               mock.patch.object(gc.sqlite3, "connect", connect)]
    if loader is not None:
        patches.append(mock.patch.object(gc, "load_adjusted_prices", loader))
    for p in patches:
        p.start()
    try:
        gc.run_backtest_golden_cross(START, END, run_id=rid, adjusted_prices=True)
        params = sink["params"]
        return json.loads(params[6]), params[5]      # trades_json, summary_text (UPDATE 파라미터 순서)
    finally:
        for p in patches:
            p.stop()
        c = connect_primary_db(timeout=60)
        c.execute("DELETE FROM backtest_runs WHERE run_id=?", (rid,))
        c.commit(); c.close()


@unittest.skipUnless(_HAS_DB, "PostgreSQL 접속 불가")
class TestGoldenCrossAdjusted(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.trades, cls.summary = _run()

    def test_runs_with_stats_and_raw_fields(self):
        self.assertGreater(len(self.trades), 0)
        self.assertIn("조정가격(W3)", self.summary)
        self.assertTrue(any(t.get("entry_price_raw") for t in self.trades))

    def _held(self):
        c = connect_primary_db(timeout=30)
        try:
            for t in self.trades:
                if t["entry_date"] >= t["exit_date"]:
                    continue
                ds = [r[0] for r in c.execute("SELECT date FROM price_history WHERE stock_code=? AND date>? AND date<? AND close>0 ORDER BY date",
                                              (t["stock_code"], t["entry_date"], t["exit_date"])).fetchall()]
                if len(ds) >= 4:
                    return t
        finally:
            c.close()
        self.fail("시험할 보유 거래가 없다")

    def _inject(self, t, kind):
        orig = bc.load_adjusted_prices

        def loader(conn, codes, start, end, **kw):
            out = orig(conn, codes, start, end, **kw)
            e = out[t["stock_code"]]
            mid = [d for d in e["dates"] if t["entry_date"] < d < t["exit_date"]]
            b = mid[len(mid) // 2]
            e["breaks"] = sorted(set(e["breaks"]) | {b})
            prev = [d for d in e["dates"] if d < b][-1]
            e["break_disclosed"] = dict(e.get("break_disclosed") or {}); e["break_disclosed"][b] = prev if kind == "disclosed" else None
            end_ = e["dates"][min(e["dates"].index(b) + 20, len(e["dates"]) - 1)]
            e["excluded_ranges"] = list(e["excluded_ranges"]) + [(b, end_)]
            loader.b, loader.end = b, end_
            return out
        return loader

    def _mine(self, res, t):
        return [x for x in res if x["stock_code"] == t["stock_code"] and x["entry_date"] == t["entry_date"]]

    def test_disclosed_break_closes_before_event(self):
        t = self._held(); ld = self._inject(t, "disclosed")
        mine = self._mine(_run(ld)[0], t)
        self.assertEqual(len(mine), 1, mine)
        self.assertTrue(mine[0]["exit_reason"].startswith("단절 전 청산"), mine[0])
        self.assertLess(mine[0]["exit_date"], ld.b)

    def test_undisclosed_break_closes_on_break_day_as_unevaluable(self):
        t = self._held(); ld = self._inject(t, "undisclosed")
        mine = self._mine(_run(ld)[0], t)
        self.assertEqual(len(mine), 1, mine)
        self.assertTrue(mine[0]["exit_reason"].startswith("단절 당일 청산"), mine[0])
        self.assertEqual(mine[0].get("evaluation"), "unevaluable_break")
        self.assertEqual(mine[0]["exit_date"], ld.b)

    def test_no_entry_inside_excluded_range(self):
        t = self._held(); ld = self._inject(t, "disclosed")
        res, _ = _run(ld)
        for x in res:
            if x["stock_code"] == t["stock_code"]:
                self.assertFalse(ld.b <= x["entry_date"] <= ld.end, x)


if __name__ == "__main__":
    unittest.main()
