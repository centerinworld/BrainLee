"""P0-7 (docs/Stock_Strategy.md S19): run 지문에 공시일·정정로그·PIT 표 버전이 들어간다."""
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backtest_common as bc  # noqa: E402


class TestDataRevisionExtras(unittest.TestCase):
    def _conn(self):
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE fin_disclosure_dates (stock_code TEXT, avail_date TEXT)")
        c.execute("CREATE TABLE data_fix_log (id INTEGER PRIMARY KEY, run_id TEXT, table_name TEXT)")
        c.execute("CREATE TABLE financial_facts_pit (run_id TEXT)")
        return c

    def test_unrelated_fix_log_rows_do_not_change_hash(self):
        """2026-10-06: 지수·환율 창 보정(macro_window_*)·계약 감사 기록은 백테스트 입력이 아니므로 지문 불변."""
        c = self._conn()
        a = bc._data_revision_extras(c)
        c.execute("INSERT INTO data_fix_log(run_id, table_name) VALUES ('macro_window_^VIX_20261006_000000', 'price_history')")
        c.execute("INSERT INTO data_fix_log(run_id, table_name) VALUES ('contract_audit_20261006_071007', 'data_contract_check_log')")
        self.assertEqual(a, bc._data_revision_extras(c))

    def test_changes_when_inputs_change(self):
        c = self._conn()
        a = bc._data_revision_extras(c)
        c.execute("INSERT INTO data_fix_log(run_id, table_name) VALUES ('price_raw_restore_x', 'price_history')")
        b = bc._data_revision_extras(c)
        self.assertNotEqual(a, b)
        self.assertEqual(b, bc._data_revision_extras(c))  # 같은 상태 = 같은 값
        c.execute("INSERT INTO fin_disclosure_dates VALUES ('A','2024-03-12')")
        self.assertNotEqual(b, bc._data_revision_extras(c))

    def test_missing_table_is_none_not_error(self):
        c = sqlite3.connect(":memory:")
        out = bc._data_revision_extras(c)
        self.assertEqual(set(out), {"fin_disclosure_dates", "data_fix_log", "financial_facts_pit", "unit_error_excluded_periods"})
        self.assertTrue(all(v is None for v in out.values()))


class TestUnitErrorExclusion(unittest.TestCase):
    """D11: 공개일 게이팅 재무 로더 13곳은 fs_quirk:dart_unit_error 기간을 입력에서 뺀다."""

    def test_exclusion_clause_present_in_all_gated_loaders(self):
        files = ["backtest_common.py"] + [
            "backtest_strategies/%s.py" % n for n in
            "base composite earnings_conviction megatrend meta_v2 peak_easy recovery regime_adaptive se_momentum turnaround v8".split()]
        total = 0
        for rel in files:
            total += (ROOT / rel).read_text(encoding="utf-8").count("q.config_key='fs_quirk:dart_unit_error'")
        self.assertEqual(total, 13)

    def test_exclusion_sql_filters_period_rows(self):
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE financial_data (stock_code TEXT, year INT, quarter INT, is_annual INT, report_type TEXT)")
        c.execute("CREATE TABLE stock_collection_config (stock_code TEXT, config_key TEXT, config_value TEXT)")
        c.executemany("INSERT INTO financial_data VALUES (?,?,?,?,?)", [
            ("A", 2024, 1, 0, "CFS"), ("A", 2024, 2, 0, "CFS"), ("A", 2020, 4, 1, "OFS"), ("B", 2024, 1, 0, "CFS")])
        c.execute("INSERT INTO stock_collection_config VALUES ('A','fs_quirk:dart_unit_error','... 2024Q1CFS,2020YOFS')")
        src = (ROOT / "backtest_strategies/v8.py").read_text(encoding="utf-8")
        start = src.index("NOT EXISTS (SELECT 1 FROM stock_collection_config q")
        clause = src[start:src.index(" AND ((f.is_annual", start)]
        rows = c.execute("SELECT stock_code, year, quarter FROM financial_data f WHERE " + clause).fetchall()
        self.assertEqual(sorted(rows), [("A", 2024, 2), ("B", 2024, 1)])


if __name__ == "__main__":
    unittest.main()
