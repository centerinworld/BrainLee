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

    def test_sell_without_matching_buy_is_not_credited(self):
        """V6: StockEasy-mirrored positions were sold through the ledger without a ledger buy and inflated the cash balance."""
        ledger.initialize_account(self.conn, "mirror", 100_000)
        res = ledger.record_trade(
            self.conn, strategy="mirror", initial_cash=100_000, side="sell",
            stock_code="005930", stock_name="삼성전자", holding_id=7,
            quantity=10, price=1_100, ref_key="sell:orphan", occurred_at="2026-01-02", gross_profit=1_000,
        )
        self.assertFalse(res["inserted"])
        self.assertEqual(res["skipped"], "no_matching_buy")
        self.assertEqual(ledger.account_summary(self.conn, "mirror")["balance_krw"], 100_000)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM virtual_cash_ledger").fetchone()[0], 0)
        # deliberate repair path still possible
        forced = ledger.record_trade(
            self.conn, strategy="mirror", initial_cash=100_000, side="sell",
            stock_code="005930", stock_name="삼성전자", holding_id=7,
            quantity=10, price=1_100, ref_key="sell:orphan2", occurred_at="2026-01-02", gross_profit=1_000,
            allow_unmatched_sell=True,
        )
        self.assertTrue(forced["inserted"])

    def test_sell_matches_by_stock_when_holding_id_differs(self):
        """Legacy backfilled buys carry a placeholder holding_id; a positive net position of the same stock still matches."""
        ledger.record_trade(
            self.conn, strategy="legacy", initial_cash=100_000, side="buy",
            stock_code="005930", stock_name="삼성전자", holding_id=-5,
            quantity=10, price=1_000, ref_key="buy:legacy", occurred_at="2026-01-01",
        )
        res = ledger.record_trade(
            self.conn, strategy="legacy", initial_cash=100_000, side="sell",
            stock_code="005930", stock_name="삼성전자", holding_id=230,
            quantity=10, price=1_100, ref_key="sell:legacy", occurred_at="2026-01-02", gross_profit=1_000,
        )
        self.assertTrue(res["inserted"])


if __name__ == "__main__":
    unittest.main()
