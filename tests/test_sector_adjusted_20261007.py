"""W3(sector_focus) 강제 테스트 — v12·golden_cross와 같은 방식(실제 보유 거래에 단절 주입). 결과는 UPDATE 문을 가로채 DB에 쓰지 않는다."""
import json
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import backtest_common as bc  # noqa: E402
import backtest_strategies.sector as sec  # noqa: E402

try:
    from db_compat import connect_primary_db
    _c = connect_primary_db(timeout=10)
    _HAS_DB = bool(_c.execute("SELECT 1 FROM price_history LIMIT 1").fetchone())
    _c.close()
except Exception:  # pragma: no cover
    _HAS_DB = False

START, END = "2024-01-02", "2024-09-30"


class _Spy:
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
    rid = "sec_test_" + uuid.uuid4().hex[:6]
    sink = {}
    real_connect = sec.sqlite3.connect
    patches = [mock.patch.object(sec, "_record_run_spec", lambda *a, **k: None),
               mock.patch.object(sec, "_register_execution_artifacts", lambda *a, **k: None),
               mock.patch.object(sec.sqlite3, "connect", lambda *a, **k: _Spy(real_connect(*a, **k), sink))]
    if loader is not None:
        patches.append(mock.patch.object(sec, "load_adjusted_prices", loader))
    for p in patches:
        p.start()
    try:
        sec.run_backtest_sector(START, END, run_id=rid, adjusted_prices=True)
        params = sink["params"]
        return json.loads(params[5])["trades"], params[0]
    finally:
        for p in patches:
            p.stop()
        c = connect_primary_db(timeout=60)
        c.execute("DELETE FROM backtest_runs WHERE run_id=?", (rid,))
        c.commit(); c.close()


def _pairs(trades):
    """BUY→청산 짝: [(code, entry_date, exit_date, exit_trade)]"""
    open_, out = {}, []
    for t in trades:
        if t["action"] == "BUY":
            open_[t["code"]] = t["date"]
        elif t["action"] in ("SELL", "SECTOR_EXIT") and t["code"] in open_:
            out.append((t["code"], open_.pop(t["code"]), t["date"], t))
    return out


@unittest.skipUnless(_HAS_DB, "PostgreSQL 접속 불가")
class TestSectorAdjusted(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.trades, cls.summary = _run()

    def test_runs_with_stats(self):
        self.assertTrue(any(t["action"] == "BUY" for t in self.trades))
        self.assertIn("조정가격(W3)", self.summary)
        self.assertTrue(all(t.get("price_raw") and t.get("qty_raw") for t in self.trades if t["action"] == "BUY"))

    def _held(self):
        for code, a, b, t in _pairs(self.trades):
            c = connect_primary_db(timeout=30)
            try:
                ds = [r[0] for r in c.execute("SELECT date FROM price_history WHERE stock_code=? AND date>? AND date<? AND close>0 ORDER BY date", (code, a, b)).fetchall()]
            finally:
                c.close()
            if len(ds) >= 4:
                return code, a, b
        self.fail("시험할 보유 거래가 없다")

    def _inject(self, code, a, b, kind):
        orig = bc.load_adjusted_prices

        def loader(conn, codes, start, end, **kw):
            out = orig(conn, codes, start, end, **kw)
            e = out[code]
            mid = [d for d in e["dates"] if a < d < b]
            bd = mid[len(mid) // 2]
            e["breaks"] = sorted(set(e["breaks"]) | {bd})
            prev = [d for d in e["dates"] if d < bd][-1]
            e["break_disclosed"] = dict(e.get("break_disclosed") or {}); e["break_disclosed"][bd] = prev if kind == "disclosed" else None
            end_ = e["dates"][min(e["dates"].index(bd) + 20, len(e["dates"]) - 1)]
            e["excluded_ranges"] = list(e["excluded_ranges"]) + [(bd, end_)]
            loader.bd, loader.end = bd, end_
            return out
        return loader

    def test_disclosed_break_closes_before_event(self):
        code, a, b = self._held(); ld = self._inject(code, a, b, "disclosed")
        trades, _ = _run(ld)
        mine = [t for t in trades if t["code"] == code and t["action"] == "SELL" and t["date"] >= a and "단절" in t.get("reason", "")]
        self.assertTrue(mine, [t for t in trades if t["code"] == code])
        self.assertTrue(mine[0]["reason"].startswith("단절 전 청산"), mine[0])
        self.assertLess(mine[0]["date"], ld.bd)

    def test_undisclosed_break_closes_on_break_day_as_unevaluable(self):
        code, a, b = self._held(); ld = self._inject(code, a, b, "undisclosed")
        trades, _ = _run(ld)
        mine = [t for t in trades if t["code"] == code and t["action"] == "SELL" and "단절" in t.get("reason", "")]
        self.assertTrue(mine)
        self.assertTrue(mine[0]["reason"].startswith("단절 당일 청산"), mine[0])
        self.assertEqual(mine[0].get("evaluation"), "unevaluable_break")
        self.assertEqual(mine[0]["date"], ld.bd)

    def test_no_entry_inside_excluded_range(self):
        code, a, b = self._held(); ld = self._inject(code, a, b, "disclosed")
        trades, _ = _run(ld)
        for t in trades:
            if t["code"] == code and t["action"] == "BUY":
                self.assertFalse(ld.bd <= t["date"] <= ld.end, t)


if __name__ == "__main__":
    unittest.main()
