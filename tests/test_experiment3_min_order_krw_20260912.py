"""Regression test for Experiment #3 (docs/claude_handoff_strategy_code_findings_20260912.md
"허용 상한 내 현금 활용"): _execute_strategy_center_paper's buy loop should attempt a
smaller order when available cash is below one full ticket but at or above a caller-supplied
min_order_krw floor, instead of breaking outright. Default behavior (min_order_krw=None ->
STRATEGY_CENTER_PAPER_TICKET_KRW) must stay identical to before this change."""
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


class TestMinOrderKrwFloor(unittest.TestCase):
    def _make_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""CREATE TABLE peak_holding(
            id INTEGER PRIMARY KEY, stock_code TEXT, stock_name TEXT, quantity INTEGER,
            buy_price REAL, is_active INTEGER, sell_price REAL, sold_at TEXT,
            current_price REAL, profit_pct REAL, updated_at TEXT, strategy TEXT,
            sector TEXT, entry_date TEXT, hold_days INTEGER, entry_reason_text TEXT,
            entry_reason_json TEXT, entry_reason_updated_at TEXT, detected_at TEXT)""")
        conn.execute("""CREATE TABLE peak_trade(
            id INTEGER PRIMARY KEY, stock_name TEXT, tx_type TEXT, price REAL, quantity INTEGER,
            total_amount REAL, profit REAL, profit_pct REAL, tx_at TEXT, strategy TEXT)""")
        conn.commit()
        return _UnclosableConnProxy(conn)

    def _run(self, available_cash, min_order_krw):
        conn = self._make_conn()
        selected = {"source_strategy": "v2", "strategy": "sc_v2", "rank": 1, "average_return_pct": 10.0}
        with mock.patch.object(trend, "_db", return_value=conn), \
             mock.patch.object(trend, "_ensure_peak_holding_reason_columns", return_value=None), \
             mock.patch.object(trend, "_strategy_center_refresh_signal",
                                return_value={"date": "2024-01-05", "run_id": "x", "buys": [{"code": "ZZZZZZ", "reason": "sig"}], "sells": []}), \
             mock.patch.object(trend, "_investable_cash", return_value=available_cash), \
             mock.patch.object(trend, "_combo_current_price", return_value=8_000_000.0), \
             mock.patch.object(trend, "_combo_neutral_tiebreak", return_value="0"), \
             mock.patch.object(trend, "_combo_stock_name", return_value="TestCo"), \
             mock.patch.object(trend, "_paper_buy_gate", return_value={"decision": "BUY_ALLOWED"}), \
             mock.patch.object(trend, "_record_paper_trade", return_value=None):
            return trend._execute_strategy_center_paper(selected, "2024-01-05", min_order_krw=min_order_krw)

    def test_default_none_preserves_full_ticket_cliff(self):
        # available (8M) < full ticket (10M) -> loop must break under unchanged default behavior
        result = self._run(available_cash=8_000_000, min_order_krw=None)
        self.assertEqual(result["bought"], 0)

    def test_lower_floor_allows_smaller_order(self):
        # same 8M available, but caller opts into a 5M floor -> a sized-down buy should be attempted
        result = self._run(available_cash=8_000_000, min_order_krw=5_000_000)
        self.assertEqual(result["bought"], 1)

    def test_signature_default_is_ticket_krw_when_unset(self):
        import inspect
        sig = inspect.signature(trend._execute_strategy_center_paper)
        self.assertIsNone(sig.parameters["min_order_krw"].default)


if __name__ == "__main__":
    unittest.main()
