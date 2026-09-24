"""
Regression tests for F06 (docs/claude_handoff_strategy_code_findings_20260912.md):
top-five selection shortfall / drop-out must not silently stop existing-holding
management for sc_* paper accounts.
"""
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import routes.trend as trend  # noqa: E402


class _UnclosableConnProxy:
    """Wraps a sqlite3.Connection so the code-under-test's conn.close() is a no-op,
    letting the test inspect the in-memory DB's state afterward."""
    def __init__(self, real_conn):
        self._real = real_conn

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._real, name)


def _matrix(rows):
    return {"strategies": [
        {"strategy": key, "label": key, "governance": {"tier": "verified", "metrics": {"average_return_pct": ret}}}
        for key, ret in rows
    ]}


class TestSelectTopFiveStrictFlag(unittest.TestCase):
    def test_strict_true_raises_on_shortfall_unchanged_default_behavior(self):
        with mock.patch("routes.backtest.get_backtest_matrix", return_value=_matrix([("v2", 10.0), ("sector_focus", 9.0)])):
            with self.assertRaises(RuntimeError):
                trend._select_strategy_center_top_five()  # default strict=True unchanged

    def test_strict_false_returns_partial_list_instead_of_raising(self):
        with mock.patch("routes.backtest.get_backtest_matrix", return_value=_matrix([("v2", 10.0), ("sector_focus", 9.0)])):
            selected = trend._select_strategy_center_top_five(strict=False)
        self.assertEqual(len(selected), 2)
        self.assertEqual({item["source_strategy"] for item in selected}, {"v2", "sector_focus"})

    def test_strict_false_still_returns_five_when_five_available(self):
        rows = [(k, float(i)) for i, k in enumerate(trend.STRATEGY_CENTER_PAPER_ENGINES)]
        with mock.patch("routes.backtest.get_backtest_matrix", return_value=_matrix(rows)):
            selected = trend._select_strategy_center_top_five(strict=False)
        self.assertEqual(len(selected), 5)


class TestExecutePaperSellOnlyMode(unittest.TestCase):
    """allow_new_buys=False must process sells for existing holdings and take the
    early-return path before touching ANY buy-side dependency (gates, cash sizing,
    live price lookups for candidates, etc.)."""

    def _make_conn_with_one_active_holding(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""CREATE TABLE peak_holding(
            id INTEGER PRIMARY KEY, stock_code TEXT, stock_name TEXT, quantity INTEGER,
            buy_price REAL, is_active INTEGER, sell_price REAL, sold_at TEXT,
            current_price REAL, profit_pct REAL, updated_at TEXT, strategy TEXT)""")
        conn.execute("""CREATE TABLE peak_trade(
            id INTEGER PRIMARY KEY, stock_name TEXT, tx_type TEXT, price REAL, quantity INTEGER,
            total_amount REAL, profit REAL, profit_pct REAL, tx_at TEXT, strategy TEXT)""")
        conn.execute(
            "INSERT INTO peak_holding VALUES (1,'AAAAAA','TestCo',100,10000,1,NULL,NULL,10000,0,NULL,'sc_v2')"
        )
        conn.commit()
        return _UnclosableConnProxy(conn)  # keep the in-memory DB alive past the code-under-test's conn.close()

    def test_sell_only_mode_sells_and_never_buys(self):
        conn = self._make_conn_with_one_active_holding()
        selected = {"source_strategy": "v2", "strategy": "sc_v2", "rank": None, "average_return_pct": None}
        with mock.patch.object(trend, "_db", return_value=conn), \
             mock.patch.object(trend, "_ensure_peak_holding_reason_columns", return_value=None), \
             mock.patch.object(trend, "_strategy_center_refresh_signal",
                                return_value={"date": "2024-01-05", "run_id": "x",
                                               "buys": [{"code": "ZZZZZZ", "reason": "should never be reached"}],
                                               "sells": [{"code": "AAAAAA"}]}), \
             mock.patch.object(trend, "_combo_current_price", return_value=12000.0), \
             mock.patch.object(trend, "_record_paper_trade", return_value=None):
            result = trend._execute_strategy_center_paper(selected, "2024-01-05", allow_new_buys=False)

        self.assertEqual(result["sold"], 1)
        self.assertEqual(result["bought"], 0)
        self.assertFalse(result["new_buys_allowed"])
        row = conn.execute("SELECT is_active FROM peak_holding WHERE id=1").fetchone()
        self.assertEqual(row[0], 0, "the held position must be sold")
        buy_rows = conn.execute("SELECT COUNT(*) FROM peak_trade WHERE tx_type='buy'").fetchone()[0]
        self.assertEqual(buy_rows, 0, "no buy must ever be attempted when allow_new_buys=False")

    def test_default_allow_new_buys_true_preserves_prior_behavior_signature(self):
        # Not a full buy-path test (that needs the live gate/cash stack) -- just confirms
        # the default parameter value didn't flip and old positional-call sites still work.
        import inspect
        sig = inspect.signature(trend._execute_strategy_center_paper)
        self.assertTrue(sig.parameters["allow_new_buys"].default is True)


if __name__ == "__main__":
    unittest.main()
