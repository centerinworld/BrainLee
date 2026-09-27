import copy
import unittest
from services.integrity_findings import build_findings

class FindingTests(unittest.TestCase):
    def setUp(self):
        self.scan = {'db_backend':'postgresql', 'db_name':'fixture', 'db_host':'localhost', 'db_port':5432,
                     'scanned_at':'2026-09-17T12:00:00+09:00', 'tables':[{'table':'price_history',
                     'row_count':10, 'invalid_ohlc_count':2, 'high_less_than_low_count':0, 'future_dated_count':0}]}

    def test_stable_id_new_evidence(self):
        first = build_findings(self.scan)
        self.scan['scanned_at'] = '2026-09-18T12:00:00+09:00'
        second = build_findings(self.scan)
        self.assertEqual(first['findings'][0]['finding_id'], second['findings'][0]['finding_id'])
        self.assertNotEqual(first['evidence_hash'], second['evidence_hash'])
        self.assertFalse(second['code_job_eligible'])
        self.assertIn('verified_root_cause', second['findings'][0]['missing_evidence'])

    def test_database_identity_separates_candidates(self):
        first = build_findings(self.scan)
        self.scan['db_name'] = 'other'
        self.assertNotEqual(first['findings'][0]['finding_id'], build_findings(self.scan)['findings'][0]['finding_id'])

    def test_zero_is_not_goal_verified(self):
        self.scan['tables'][0]['invalid_ohlc_count'] = 0
        result = build_findings(self.scan)
        self.assertEqual(result['findings'], [])
        self.assertFalse(result['goal_verified'])
        self.assertEqual(len(result['coverage']['tables']), 1)

    def test_prose_rejected(self):
        for data in ('looks correct', {}, {'db_backend':'sqlite'}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                build_findings(data)

    def test_invalid_counts(self):
        for value in (-1, 11, True, '2', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                data = copy.deepcopy(self.scan)
                data['tables'][0]['invalid_ohlc_count'] = value
                build_findings(data)

    def test_duplicate_table(self):
        self.scan['tables'].append(copy.deepcopy(self.scan['tables'][0]))
        with self.assertRaises(ValueError):
            build_findings(self.scan)
