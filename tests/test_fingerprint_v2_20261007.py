"""D16(REVIEW_PLAN §29-2): 데이터 지문은 행별 NUMERIC 정확 합계 — 행 순서가 달라도 같은 값, 1원 정정은 잡힌다."""
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import run_registry as rr  # noqa: E402


def _conn(rows):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE price_history (id INTEGER PRIMARY KEY, stock_code TEXT, date TEXT, close REAL, volume REAL, created_at TEXT)")
    c.execute("CREATE TABLE financial_data (year INT, quarter INT, updated_at TEXT)")
    c.execute("CREATE TABLE security_master_history (updated_at TEXT)")
    c.executemany("INSERT INTO price_history VALUES (?,?,?,?,?,?)", rows)
    return c


ROWS = [(i + 1, "A%03d" % i, "2020-01-%02d" % (i % 28 + 1), 1000.1 + i * 0.3, 10.0 * i, "x") for i in range(200)]


class TestFingerprintV2(unittest.TestCase):
    def test_version_marked(self):
        s = rr.source_snapshot(_conn(ROWS), "2020-01-01", "2020-12-31")
        self.assertEqual(s["fingerprint_version"], "v2")
        self.assertEqual(s["datasets"]["fingerprint_version"], "v2")

    def test_row_order_independent(self):
        a = rr.source_snapshot(_conn(ROWS), "2020-01-01", "2020-12-31")["fingerprint"]
        b = rr.source_snapshot(_conn(list(reversed(ROWS))), "2020-01-01", "2020-12-31")["fingerprint"]
        self.assertEqual(a, b)

    def test_one_won_correction_detected(self):
        changed = list(ROWS)
        r = changed[5]
        changed[5] = (r[0], r[1], r[2], r[3] + 1.0, r[4], r[5])
        a = rr.source_snapshot(_conn(ROWS), "2020-01-01", "2020-12-31")["fingerprint"]
        b = rr.source_snapshot(_conn(changed), "2020-01-01", "2020-12-31")["fingerprint"]
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
