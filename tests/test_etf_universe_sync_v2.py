import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ETF_check"))

from direct_etf_pipeline import DatabaseManager, ETFMeta  # noqa: E402
from etf_universe_sync import sync_universe  # noqa: E402
from etf_universe_sync_v2 import existing_universe, get_or_sync_universe  # noqa: E402


class Source:
    def universe(self):
        return [
            ETFMeta(f"{index:06d}", f"ETF {index}", "KOSPI", f"KR7{index:06d}0000"[:12])
            for index in range(120)
        ]


class ImmutableUniverseTest(unittest.TestCase):
    def test_existing_snapshot_is_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "etf.db"
            DatabaseManager(path)
            conn = sqlite3.connect(path)
            sync_universe(conn, "20260828", Source())
            existing = existing_universe(conn, "20260828")
            self.assertEqual(existing["count"], 120)
            self.assertTrue(existing["reused"])
            self.assertEqual(get_or_sync_universe(conn, "20260828"), existing)
            conn.close()


if __name__ == "__main__":
    unittest.main()
