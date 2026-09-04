import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ETF_check"))

from direct_etf_pipeline import DatabaseManager, ETFMeta  # noqa: E402
from etf_universe_sync import dated_universe, sync_universe  # noqa: E402


class Source:
    def __init__(self, rows):
        self.rows = rows

    def universe(self):
        return self.rows


def rows(count, start=0):
    return [
        ETFMeta(f"{index:06d}", f"ETF {index}", "KOSPI", f"KR7{index:06d}0000"[:12])
        for index in range(start, start + count)
    ]


class UniverseSyncTest(unittest.TestCase):
    def test_snapshot_is_date_pinned_and_absent_ticker_is_deactivated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "etf.db"
            DatabaseManager(path)
            conn = sqlite3.connect(path)
            sync_universe(conn, "20260828", Source(rows(120)))
            changed = rows(119) + rows(1, 500)
            result = sync_universe(conn, "20260829", Source(changed))
            self.assertEqual(result["count"], 120)
            self.assertEqual(result["added"], ["000500"])
            self.assertEqual(result["removed"], ["000119"])
            self.assertEqual(len(dated_universe(conn, "20260828")), 120)
            active = conn.execute(
                "SELECT is_active FROM etf_meta WHERE etf_ticker='000119'"
            ).fetchone()[0]
            self.assertEqual(active, 0)
            conn.close()

    def test_abnormal_universe_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "etf.db"
            DatabaseManager(path)
            conn = sqlite3.connect(path)
            sync_universe(conn, "20260828", Source(rows(120)))
            with self.assertRaisesRegex(RuntimeError, "changed abnormally"):
                sync_universe(conn, "20260829", Source(rows(140)))
            count = conn.execute(
                "SELECT COUNT(*) FROM etf_universe_daily WHERE base_date='20260829'"
            ).fetchone()[0]
            self.assertEqual(count, 0)
            conn.close()


if __name__ == "__main__":
    unittest.main()
