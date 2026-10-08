"""W5b(earnings_conviction) 강제 테스트 — golden_cross(W3) 테스트를 같은 방식으로 옮김 — v12 테스트와 같은 방식: 실제 데이터로 한 번 돌려 보유한 거래를 고르고, 그 보유 구간에 단절을 주입한다.
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
import backtest_strategies.earnings_conviction as gc  # noqa: E402

try:
    from db_compat import connect_primary_db
    _c = connect_primary_db(timeout=10)
    _HAS_DB = bool(_c.execute("SELECT 1 FROM price_history LIMIT 1").fetchone())
    _c.close()
except Exception:  # pragma: no cover
    _HAS_DB = False

START, END = "2020-03-02", "2021-03-31"


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
    rid = "ec_test_" + uuid.uuid4().hex[:6]
    sink = {}
    real_connect = gc.sqlite3.connect

    def connect(*a, **k):
        return _Spy(real_connect(*a, **k), sink)

    c = connect_primary_db(timeout=60)
    c.execute("INSERT INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,status) VALUES (?,?,?,?,?,?,'running')",
              (rid, "ec-engine-test", "earnings_conviction", START, END, 10_000_000))
    c.commit(); c.close()
    patches = [mock.patch.object(gc, "_record_run_spec", lambda *a, **k: None),
               mock.patch.object(gc, "_register_execution_artifacts", lambda *a, **k: None),
               mock.patch.object(gc.sqlite3, "connect", connect)]
    if loader is not None:
        patches.append(mock.patch.object(gc, "load_adjusted_prices", loader))
    for p in patches:
        p.start()
    try:
        gc.run_backtest_earnings_conviction(START, END, run_id=rid, adjusted_prices=True)
        params = sink["params"]
        return json.loads(params[5]), params[4]      # trades_json, summary_text (UPDATE 파라미터 순서)
    finally:
        for p in patches:
            p.stop()
        c = connect_primary_db(timeout=60)
        c.execute("DELETE FROM backtest_runs WHERE run_id=?", (rid,))
        c.commit(); c.close()


@unittest.skipUnless(_HAS_DB, "PostgreSQL 접속 불가")
class TestEarningsConvictionAdjusted(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.trades, cls.summary = _run()

    def test_runs_with_stats_and_raw_fields(self):
        self.assertGreater(len(self.trades), 0)
        self.assertIn("조정가격(W5b)", self.summary)
        self.assertTrue(any(t.get("entry_raw") for t in self.trades if "sell_date" in t))

    def _held(self):
        c = connect_primary_db(timeout=30)
        try:
            for t in self.trades:
                if "sell_date" not in t or t["buy_date"] >= t["sell_date"]:
                    continue
                ds = [r[0] for r in c.execute("SELECT date FROM price_history WHERE stock_code=? AND date>? AND date<? AND close>0 ORDER BY date",
                                              (t["code"], t["buy_date"], t["sell_date"])).fetchall()]
                if len(ds) >= 4:
                    return t
        finally:
            c.close()
        self.fail("시험할 보유 거래가 없다")

    def _inject(self, t, kind):
        orig = bc.load_adjusted_prices

        def loader(conn, codes, start, end, **kw):
            out = orig(conn, codes, start, end, **kw)
            e = out[t["code"]]
            mid = [d for d in e["dates"] if t["buy_date"] < d < t["sell_date"]]
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
        return [x for x in res if x["code"] == t["code"] and x["buy_date"] == t["buy_date"] and "sell_date" in x]

    def test_disclosed_break_closes_before_event(self):
        t = self._held(); ld = self._inject(t, "disclosed")
        mine = self._mine(_run(ld)[0], t)
        self.assertEqual(len(mine), 1, mine)
        self.assertTrue(mine[0]["reason"].startswith("단절 전 청산"), mine[0])
        self.assertLess(mine[0]["sell_date"], ld.b)

    def test_undisclosed_break_closes_on_break_day_as_unevaluable(self):
        t = self._held(); ld = self._inject(t, "undisclosed")
        mine = self._mine(_run(ld)[0], t)
        self.assertEqual(len(mine), 1, mine)
        self.assertTrue(mine[0]["reason"].startswith("단절 당일 청산"), mine[0])
        self.assertEqual(mine[0].get("evaluation"), "unevaluable_break")
        self.assertEqual(mine[0]["sell_date"], ld.b)

    def test_no_entry_inside_excluded_range(self):
        t = self._held(); ld = self._inject(t, "disclosed")
        res, _ = _run(ld)
        for x in res:
            if x["code"] == t["code"]:
                self.assertFalse(ld.b <= x["buy_date"] <= ld.end, x)


if __name__ == "__main__":
    unittest.main()
