"""Regression tests for the orphan-account discovery fix
(docs/claude_resume_strategy_review_20260912.md priority 3, Codex review): sc_* accounts
whose adapter was removed from STRATEGY_CENTER_PAPER_ENGINES must still be discovered (as
orphaned, flagged for manual attention) instead of being silently skipped because the old
code only iterated the current adapter dict."""
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import routes.trend as trend  # noqa: E402


class _UnclosableConnProxy:
    def __init__(self, real_conn):
        self._real = real_conn

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._real, name)


class TestOrphanAccountDiscovery(unittest.TestCase):
    def _make_conn(self, strategies_with_holdings):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""CREATE TABLE peak_holding(
            id INTEGER PRIMARY KEY, stock_code TEXT, stock_name TEXT, quantity INTEGER,
            buy_price REAL, is_active INTEGER, strategy TEXT)""")
        for i, strat in enumerate(strategies_with_holdings):
            conn.execute("INSERT INTO peak_holding VALUES (?,?,?,?,?,1,?)",
                          (i + 1, "AAAAAA", "TestCo", 10, 1000, strat))
        conn.commit()
        return _UnclosableConnProxy(conn)

    def test_orphan_strategy_not_in_engines_is_reported_not_silently_dropped(self):
        # "sc_removed_engine" has active holdings but no matching entry in
        # STRATEGY_CENTER_PAPER_ENGINES (simulating a since-deleted adapter).
        conn = self._make_conn(["sc_removed_engine"])
        with mock.patch.object(trend, "_select_strategy_center_top_five", return_value=[]), \
             mock.patch.object(trend, "_db", return_value=conn), \
             mock.patch.object(trend, "_combo_latest_trading_day", return_value="2024-01-05"), \
             mock.patch.object(trend, "STRATEGY_CENTER_PAPER_ENGINES", {}):
            result = trend.execute_strategy_center_top_five_now()
        self.assertIn("sc_removed_engine", result["orphaned_accounts"])
        self.assertEqual(result["sell_only_managed"], [])

    def test_known_dropped_out_strategy_still_goes_to_sell_only_not_orphaned(self):
        conn = self._make_conn(["sc_v2"])

        def fake_engine(*a, **kw):
            return "run_id_x"

        with mock.patch.object(trend, "_select_strategy_center_top_five", return_value=[]), \
             mock.patch.object(trend, "_db", return_value=conn), \
             mock.patch.object(trend, "_combo_latest_trading_day", return_value="2024-01-05"), \
             mock.patch.object(trend, "STRATEGY_CENTER_PAPER_ENGINES", {"v2": (fake_engine, {"max_positions": 10})}), \
             mock.patch.object(trend, "_execute_strategy_center_paper",
                                return_value={"ok": True, "sold": 0, "bought": 0}), \
             mock.patch.object(trend, "_record_virtual_strategy_run", return_value=None):
            result = trend.execute_strategy_center_top_five_now()
        self.assertEqual(result["orphaned_accounts"], [])
        self.assertEqual(len(result["sell_only_managed"]), 1)
        self.assertEqual(result["sell_only_managed"][0]["source_strategy"], "v2")


if __name__ == "__main__":
    unittest.main()
