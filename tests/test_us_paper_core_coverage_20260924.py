import sqlite3
import unittest
from datetime import date, timedelta

from routes.us_virtual_trading import _latest_broad_us_date, _latest_us_price_coverage, _us_core_session_coverage


class USPaperCoreCoverageTests(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:')
        self.c.row_factory = sqlite3.Row
        self.c.execute('CREATE TABLE us_price_history(ticker TEXT,date TEXT,close REAL,volume REAL,created_at TEXT,open REAL,high REAL,low REAL)')
        self.days = [(date(2026, 7, 1) + timedelta(days=i)).isoformat() for i in range(60)]
        # 100 core names print every session; 40 SPAC-like names print on every 3rd session only
        for d in self.days:
            for i in range(100):
                self.c.execute('INSERT INTO us_price_history(ticker,date,close) VALUES(?,?,1)', (f'C{i:03d}', d))
        for n, d in enumerate(self.days):
            if n % 3 == 0:
                for i in range(40):
                    self.c.execute('INSERT INTO us_price_history(ticker,date,close) VALUES(?,?,1)', (f'S{i:03d}', d))

    def drop_from_latest(self, n):
        self.c.execute('DELETE FROM us_price_history WHERE date=? AND ticker IN (SELECT ticker FROM us_price_history WHERE date=? AND ticker LIKE "C%" LIMIT ?)',
                       (self.days[-1], self.days[-1], n))

    def test_illiquid_names_do_not_count_toward_core_universe(self):
        core, _ = _us_core_session_coverage(self.c)
        self.assertEqual(core, 100)

    def test_complete_session_is_accepted_even_when_history_had_a_bigger_day(self):
        # an old all-time-peak day with 140 names must not raise the bar (previous rule compared against it)
        self.drop_from_latest(3)
        self.assertEqual(_latest_broad_us_date(self.c), self.days[-1])
        self.assertTrue(_latest_us_price_coverage(self.c)['ready'])

    def test_genuinely_partial_session_falls_back_to_previous_session(self):
        self.drop_from_latest(10)  # 90% of core < 95%
        self.assertEqual(_latest_broad_us_date(self.c), self.days[-2])
        self.assertFalse(_latest_us_price_coverage(self.c)['ready'])


if __name__ == '__main__':
    unittest.main()
