import sqlite3
import unittest

import backtest_equity as be


class BacktestEquityTests(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:')
        self.c.execute("CREATE TABLE backtest_equity_curve (run_id TEXT NOT NULL, date TEXT NOT NULL, equity REAL NOT NULL, "
                       "source TEXT NOT NULL, PRIMARY KEY(run_id, source, date))")

    def test_curve_from_result_reads_engine_curve_and_skips_bad_points(self):
        r = {"equity_curve": [{"date": "2026-01-02", "equity": 100}, {"date": "2026-01-05", "equity": "101.5"}, {"bad": 1}, None]}
        self.assertEqual(be.curve_from_result(r), [("2026-01-02", 100.0), ("2026-01-05", 101.5)])
        self.assertEqual(be.curve_from_result({}), [])

    def test_store_curve_keeps_one_curve_per_source(self):
        be.store_curve(self.c, "r1", [("2026-01-02", 100), ("2026-01-05", 110)], "realized_pnl")
        be.store_curve(self.c, "r1", [("2026-01-02", 100), ("2026-01-05", 108)], "mtm_reconstructed")
        # re-storing a source replaces only that source
        be.store_curve(self.c, "r1", [("2026-01-02", 100), ("2026-01-05", 111)], "realized_pnl")
        rows = dict(((s, d), v) for d, v, s in self.c.execute("SELECT date,equity,source FROM backtest_equity_curve WHERE run_id='r1'"))
        self.assertEqual(rows[("realized_pnl", "2026-01-05")], 111.0)
        self.assertEqual(rows[("mtm_reconstructed", "2026-01-05")], 108.0)
        self.assertEqual(len(rows), 4)

    def test_pick_handles_the_three_trade_schemas(self):
        a = {"stock_code": "1", "entry_date": "2025-01-02", "exit_date": "2025-02-03", "entry_price": 10, "profit_amt": 5}
        b = {"code": "1", "buy_date": "2025-01-02", "sell_date": "2025-02-03", "entry": 10.0, "exit": 12.0, "pnl": 5}
        c = {"sc": "1", "entry": "2025-01-02", "exit": "2025-02-03", "entry_price": 10.0, "pnl": 5}
        for t in (a, b, c):
            self.assertEqual(be._pick(t, "code"), t.get("stock_code") or t.get("code") or t.get("sc"))
            self.assertEqual(be._pick(t, "entry"), "2025-01-02")
            self.assertEqual(be._pick(t, "exit"), "2025-02-03")
            self.assertEqual(be._pick(t, "ep"), 10 if "entry_price" in t else 10.0)
            self.assertEqual(be._pick(t, "pnl"), 5)

    def test_source_priority_order_is_documented(self):
        self.assertEqual(be.SOURCES_BY_PRIORITY[0], "engine")
        self.assertEqual(be.SOURCES_BY_PRIORITY[-1], "realized_pnl_assumed_100m")


if __name__ == '__main__':
    unittest.main()
