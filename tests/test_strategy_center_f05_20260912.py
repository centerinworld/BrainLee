"""Regression tests for F05 (docs/claude_handoff_strategy_code_findings_20260912.md):
paper trading replay must reuse the backtest engine's own recorded fill price for a
signal instead of re-fetching "today's latest close", and the signal cache key must be
sensitive to engine kwargs/source-code changes, not just source_strategy+date."""
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


class TestF05SignalPriceReuse(unittest.TestCase):
    def _make_conn_with_one_holding(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""CREATE TABLE peak_holding(
            id INTEGER PRIMARY KEY, stock_code TEXT, stock_name TEXT, quantity INTEGER,
            buy_price REAL, is_active INTEGER, sell_price REAL, sold_at TEXT,
            current_price REAL, profit_pct REAL, updated_at TEXT, strategy TEXT)""")
        conn.execute("""CREATE TABLE peak_trade(
            id INTEGER PRIMARY KEY, stock_name TEXT, tx_type TEXT, price REAL, quantity INTEGER,
            total_amount REAL, profit REAL, profit_pct REAL, tx_at TEXT, strategy TEXT)""")
        conn.execute("INSERT INTO peak_holding VALUES (1,'AAAAAA','TestCo',100,10000,1,NULL,NULL,10000,0,NULL,'sc_v2')")
        conn.commit()
        return _UnclosableConnProxy(conn)

    def test_sell_uses_signal_price_not_latest_close(self):
        conn = self._make_conn_with_one_holding()
        selected = {"source_strategy": "v2", "strategy": "sc_v2", "rank": 1, "average_return_pct": 5.0}
        # signal's own recorded (D+1-open) fill price is 11000; "today's latest close" would be 99999
        with mock.patch.object(trend, "_db", return_value=conn), \
             mock.patch.object(trend, "_ensure_peak_holding_reason_columns", return_value=None), \
             mock.patch.object(trend, "_strategy_center_refresh_signal",
                                return_value={"date": "2024-01-05", "run_id": "x",
                                               "buys": [], "sells": [{"code": "AAAAAA", "price": 11000.0}]}), \
             mock.patch.object(trend, "_combo_current_price", return_value=99999.0), \
             mock.patch.object(trend, "_record_paper_trade", return_value=None):
            result = trend._execute_strategy_center_paper(selected, "2024-01-05", allow_new_buys=False)
        self.assertEqual(result["sold"], 1)
        row = conn.execute("SELECT sell_price FROM peak_holding WHERE id=1").fetchone()
        self.assertEqual(row[0], 11000.0, "must fill at the signal's own recorded price, not _combo_current_price")

    def test_sell_falls_back_to_latest_price_only_if_signal_price_missing(self):
        conn = self._make_conn_with_one_holding()
        selected = {"source_strategy": "v2", "strategy": "sc_v2", "rank": 1, "average_return_pct": 5.0}
        with mock.patch.object(trend, "_db", return_value=conn), \
             mock.patch.object(trend, "_ensure_peak_holding_reason_columns", return_value=None), \
             mock.patch.object(trend, "_strategy_center_refresh_signal",
                                return_value={"date": "2024-01-05", "run_id": "x",
                                               "buys": [], "sells": [{"code": "AAAAAA", "price": 0}]}), \
             mock.patch.object(trend, "_combo_current_price", return_value=12345.0), \
             mock.patch.object(trend, "_record_paper_trade", return_value=None):
            result = trend._execute_strategy_center_paper(selected, "2024-01-05", allow_new_buys=False)
        row = conn.execute("SELECT sell_price FROM peak_holding WHERE id=1").fetchone()
        self.assertEqual(row[0], 12345.0, "fallback to latest price only when the signal itself has no usable price")


class TestF05CacheFingerprint(unittest.TestCase):
    def test_cache_key_changes_when_kwargs_change(self):
        trend._strategy_center_paper_cache.clear()

        def fake_engine_a(start, end, run_name=None, **kw):
            return "run_a"

        with mock.patch.object(trend, "STRATEGY_CENTER_PAPER_ENGINES", {"v2": (fake_engine_a, {"max_positions": 10})}), \
             mock.patch.object(trend, "_combo_parse_trades", return_value=[]):
            conn = mock.MagicMock()
            conn.execute.return_value.fetchone.return_value = ("[]",)
            key_before = {k for k in trend._strategy_center_paper_cache}
            trend._strategy_center_refresh_signal(conn, "v2", "2024-01-05")
            keys_10 = set(trend._strategy_center_paper_cache) - key_before

        with mock.patch.object(trend, "STRATEGY_CENTER_PAPER_ENGINES", {"v2": (fake_engine_a, {"max_positions": 20})}), \
             mock.patch.object(trend, "_combo_parse_trades", return_value=[]):
            conn = mock.MagicMock()
            conn.execute.return_value.fetchone.return_value = ("[]",)
            trend._strategy_center_refresh_signal(conn, "v2", "2024-01-05")
            keys_20 = set(trend._strategy_center_paper_cache) - key_before - keys_10

        self.assertTrue(keys_10, "first call should have cached something")
        self.assertTrue(keys_20, "changing max_positions kwarg must produce a different cache key, not reuse the old entry")
        self.assertNotEqual(keys_10, keys_20)


if __name__ == "__main__":
    unittest.main()
