import unittest
from unittest.mock import patch

import pandas as pd

import marcap_client
from price_integrity import refresh_calendar
from scripts.audit_price_jumps_and_build_canonical import run
from tests.test_price_integrity import IntegrityTests


class AuditExtensionTests(unittest.TestCase):
    setUp = IntegrityTests.setUp
    add = IntegrityTests.add

    def _audit_rows(self):
        with patch('pathlib.Path.write_text'):
            run(self.c)
        return [tuple(r) for r in self.c.execute(
            'SELECT event_date,classification,return_usable FROM price_jump_audit ORDER BY event_date')]

    def test_refresh_calendar_is_incremental_unless_full(self):
        """2026-09-24: the unconditional full-table scan timed out on 10M+ rows; default now only looks
        back 45 days from the calendar's latest date, full=True keeps the old exhaustive behaviour."""
        for i in range(100):
            self.add('2014-01-06', code=f'{i:06d}')
        refresh_calendar(self.c)
        self.assertIsNone(self.c.execute("SELECT 1 FROM price_trading_calendar WHERE date='2014-01-06'").fetchone())
        refresh_calendar(self.c, full=True)
        self.assertIsNotNone(self.c.execute("SELECT 1 FROM price_trading_calendar WHERE date='2014-01-06'").fetchone())

    def test_share_count_evidence_requires_share_change_offsetting_price(self):
        days = pd.date_range('2026-01-01', '2026-03-31').strftime('%Y-%m-%d')
        def series(before, after):
            return {'000001': pd.DataFrame({'Date': days, 'Close': 1.0,
                                             'Stocks': [before if d < '2026-02-15' else after for d in days]})}
        with patch.object(marcap_client, '_share_series', return_value=series(1000, 2000)):
            ev = marcap_client.share_count_evidence('000001', '2026-02-15', 0.5)
            self.assertAlmostEqual(ev['share_ratio'], 2.0)
            self.assertIsNone(marcap_client.share_count_evidence('000001', '2026-02-15', 0.9))
            self.assertIsNone(marcap_client.share_count_evidence('999999', '2026-02-15', 0.5))
        with patch.object(marcap_client, '_share_series', return_value=series(1000, 1000)):
            self.assertIsNone(marcap_client.share_count_evidence('000001', '2026-02-15', 0.5))

    def test_share_count_evidence_labels_jump_but_never_makes_it_return_usable(self):
        self.add('2026-01-02'); self.add('2026-01-05', 150)
        evidence = {'shares_before': 1.0, 'shares_after': 2.0, 'share_ratio': 2.0,
                    'before_date': '2025-12-01', 'after_date': '2026-02-01'}
        with patch('scripts.audit_price_jumps_and_build_canonical.share_count_evidence', return_value=evidence):
            rows = self._audit_rows()
        self.assertEqual(rows, [('2026-01-05', 'corporate_action_share_count_evidence', 0)])

    def test_reviewed_coverage_gap_is_recorded_not_requeued(self):
        self.add('2026-01-02'); self.add('2026-01-06', 105)
        self.assertEqual(self._audit_rows()[0][1], 'coverage_gap')
        self.c.execute("INSERT INTO price_coverage_gap_reviewed VALUES('005930','2026-01-06','2026-01-02',"
                       "'trading_halt','DART 거래정지 rcept x','now')")
        self.assertEqual(self._audit_rows(), [('2026-01-06', 'coverage_gap_reviewed', 0)])


if __name__ == '__main__':
    unittest.main()
