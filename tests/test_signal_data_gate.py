"""
Failing tests (RED) for the P1 fail-closed read gate.

Contract under test: `signal_data_gate.py` (NOT YET IMPLEMENTED).
Spec: `docs/codex_handoff_p1_read_gate_spec_20260920.md`.

G1: a quarantined price row (`price_integrity_quarantine`) must be excluded from
    `read_prices_failclosed` AND reported in the exclusion summary (reason + count).
G2: `read_financials_failclosed` must read `canonical_financial_data` only — a raw
    `financial_data` row that failed the write gate (BS identity) must not surface.

Run:
  python3 -m pytest tests/test_signal_data_gate.py -q
"""
import sqlite3
import unittest

from price_integrity import ensure_schema, rebuild_views
from scripts.audit_price_jumps_and_build_canonical import DDL

# RED: import fails until signal_data_gate.py is implemented.
from signal_data_gate import read_prices_failclosed, read_financials_failclosed  # noqa: E402,F401


class SignalDataGateTests(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(":memory:")
        self.c.row_factory = sqlite3.Row
        self.c.executescript(
            "CREATE TABLE price_history(id INTEGER PRIMARY KEY, stock_code TEXT, date TEXT,"
            " open REAL, high REAL, low REAL, close REAL, volume REAL);"
            "CREATE TABLE financial_data(id INTEGER PRIMARY KEY, stock_code TEXT, year INTEGER,"
            " quarter INTEGER, is_annual INTEGER, report_type TEXT, revenue REAL,"
            " operating_profit REAL, net_income REAL, total_assets REAL,"
            " total_liabilities REAL, total_equity REAL);"
        )
        self.c.executescript(DDL)  # price_jump_audit (+ canonical view dependencies)
        ensure_schema(self.c)      # quarantine + calendar tables
        self.c.execute(
            "CREATE TABLE canonical_financial_data("
            "id INTEGER PRIMARY KEY, stock_code TEXT, year INTEGER, quarter INTEGER,"
            " is_annual INTEGER, report_type TEXT, revenue REAL, operating_profit REAL,"
            " net_income REAL, total_assets REAL, total_liabilities REAL, total_equity REAL,"
            " updated_at TEXT)"
        )
        self.c.executemany(
            "INSERT INTO price_trading_calendar(date) VALUES(?)",
            [(d,) for d in ("2026-01-05", "2026-01-06", "2026-01-07")],
        )
        rebuild_views(self.c)

    def _add_price(self, d, close, code="005930"):
        self.c.execute(
            "INSERT INTO price_history(stock_code,date,open,high,low,close,volume)"
            " VALUES(?,?,?,?,?,?,?)",
            (code, d, close, close, close, close, 10),
        )

    # ---- G1: price fail-closed ------------------------------------------------
    def test_quarantined_price_row_is_excluded_and_reported(self):
        self._add_price("2026-01-05", 100)
        self._add_price("2026-01-06", 110)
        self.c.execute(
            "INSERT INTO price_integrity_quarantine(stock_code,event_date,reason,evidence,created_at)"
            " VALUES('005930','2026-01-06','basis','','now')"
        )
        res = read_prices_failclosed(self.c, "005930", "2026-01-05", "2026-01-06")
        dates = [r[0] for r in res.rows]
        self.assertEqual(dates, ["2026-01-05"])            # quarantined row excluded
        self.assertEqual(res.total_in_window, 2)
        self.assertEqual(res.usable, 1)
        self.assertGreaterEqual(res.excluded.get("quarantined_basis", 0), 1)

    def test_clean_price_rows_are_all_returned(self):
        self._add_price("2026-01-05", 100)
        self._add_price("2026-01-06", 101)
        res = read_prices_failclosed(self.c, "005930", "2026-01-05", "2026-01-06")
        self.assertEqual(res.total_in_window, 2)
        self.assertEqual(res.usable, 2)
        self.assertEqual(res.excluded, {})

    # ---- G2: financial fail-closed -------------------------------------------
    def test_financial_read_uses_canonical_not_raw(self):
        # raw: valid row (id=1) + BS-identity-violating row (id=2: 1000 != 600 + 0)
        self.c.execute(
            "INSERT INTO financial_data(id,stock_code,year,quarter,is_annual,report_type,"
            " revenue,operating_profit,net_income,total_assets,total_liabilities,total_equity)"
            " VALUES(1,'005930',2025,1,0,'CFS',100,10,8,1000,600,400)")
        self.c.execute(
            "INSERT INTO financial_data(id,stock_code,year,quarter,is_annual,report_type,"
            " revenue,operating_profit,net_income,total_assets,total_liabilities,total_equity)"
            " VALUES(2,'005930',2025,1,0,'CFS',999,10,8,1000,600,0)")
        # canonical: only the valid row survived the write gate
        self.c.execute(
            "INSERT INTO canonical_financial_data(id,stock_code,year,quarter,is_annual,report_type,"
            " revenue,operating_profit,net_income,total_assets,total_liabilities,total_equity,updated_at)"
            " VALUES(1,'005930',2025,1,0,'CFS',100,10,8,1000,600,400,'2026-01-01T00:00:00')")
        rows = read_financials_failclosed(self.c, "005930", year=2025, quarter=1)
        self.assertEqual(len(rows), 1)
        # only the canonical row surfaces; the violating raw row never does
        self.assertEqual(rows[0]["revenue"], 100)
        self.assertEqual(rows[0]["total_equity"], 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
