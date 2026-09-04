from __future__ import annotations

import sqlite3
import unittest

import virtual_trading_ledger as ledger


class VirtualTradingLedgerTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.previous_ready = ledger._SCHEMA_READY
        ledger._SCHEMA_READY = False
        self.conn.executescript(ledger.DDL)
        ledger._SCHEMA_READY = True

    def tearDown(self):
        self.conn.close()
        ledger._SCHEMA_READY = self.previous_ready

    def test_round_trip_deducts_all_costs_and_is_idempotent(self):
        buy = ledger.record_trade(
            self.conn, strategy="test", initial_cash=100_000, side="buy",
            stock_code="005930", stock_name="삼성전자", holding_id=1,
            quantity=10, price=1_000, ref_key="buy:1", occurred_at="2026-01-01",
        )
        sell = ledger.record_trade(
            self.conn, strategy="test", initial_cash=100_000, side="sell",
            stock_code="005930", stock_name="삼성전자", holding_id=1,
            quantity=10, price=1_100, ref_key="sell:1", occurred_at="2026-01-02",
            gross_profit=1_000,
        )
        duplicate = ledger.record_trade(
            self.conn, strategy="test", initial_cash=100_000, side="sell",
            stock_code="005930", stock_name="삼성전자", holding_id=1,
            quantity=10, price=1_100, ref_key="sell:1", occurred_at="2026-01-02",
            gross_profit=1_000,
        )
        summary = ledger.account_summary(self.conn, "test")

        self.assertTrue(buy["inserted"])
        self.assertTrue(sell["inserted"])
        self.assertFalse(duplicate["inserted"])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM virtual_cash_ledger").fetchone()[0], 2
        )
        self.assertGreater(summary["total_fees"], 0)
        self.assertGreater(summary["total_taxes"], 0)
        self.assertGreater(summary["total_slippage"], 0)
        self.assertAlmostEqual(
            summary["balance_krw"], 100_000 + summary["realized_pnl_net"], places=6
        )

    def test_overdraft_is_rejected_without_ledger_entry(self):
        with self.assertRaisesRegex(ValueError, "negative"):
            ledger.record_trade(
                self.conn, strategy="test", initial_cash=1_000, side="buy",
                stock_code="005930", stock_name="삼성전자", holding_id=1,
                quantity=2, price=1_000, ref_key="buy:overdraft", occurred_at="2026-01-01",
            )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM virtual_cash_ledger").fetchone()[0], 0
        )


if __name__ == "__main__":
    unittest.main()
