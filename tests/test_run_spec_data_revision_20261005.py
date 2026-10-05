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
        c.execute("CREATE TABLE data_fix_log (id INTEGER PRIMARY KEY, run_id TEXT)")
        c.execute("CREATE TABLE financial_facts_pit (run_id TEXT)")
        return c

    def test_changes_when_inputs_change(self):
        c = self._conn()
        a = bc._data_revision_extras(c)
        c.execute("INSERT INTO data_fix_log(run_id) VALUES ('fix1')")
        b = bc._data_revision_extras(c)
        self.assertNotEqual(a, b)
        self.assertEqual(b, bc._data_revision_extras(c))  # 같은 상태 = 같은 값
        c.execute("INSERT INTO fin_disclosure_dates VALUES ('A','2024-03-12')")
        self.assertNotEqual(b, bc._data_revision_extras(c))

    def test_missing_table_is_none_not_error(self):
        c = sqlite3.connect(":memory:")
        out = bc._data_revision_extras(c)
        self.assertEqual(set(out), {"fin_disclosure_dates", "data_fix_log", "financial_facts_pit"})
        self.assertTrue(all(v is None for v in out.values()))


if __name__ == "__main__":
    unittest.main()
