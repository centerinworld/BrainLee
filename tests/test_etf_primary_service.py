import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ETF_check"))

from etf_primary_service import latest_published_day, stock_name_for  # noqa: E402


class ETFPrimaryServiceTest(unittest.TestCase):
    def test_latest_gated_publication_includes_issuer_exception_date(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = sqlite3.connect(Path(directory) / "etf.db")
            conn.execute(
                """
                CREATE TABLE etf_direct_stock_publication(
                    base_date TEXT PRIMARY KEY,status TEXT NOT NULL
                )
                """
            )
            conn.executemany(
                "INSERT INTO etf_direct_stock_publication VALUES(?,?)",
                [("20260909", "published"), ("20260911", "published")],
            )
            self.assertEqual(latest_published_day(conn), "20260911")
            conn.close()

    def test_stock_name_uses_local_meta_cache_when_available(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE etf_stock_meta(stock_code TEXT PRIMARY KEY, stock_name TEXT)"
        )
        conn.execute("INSERT INTO etf_stock_meta VALUES(?,?)", ("005930", "삼성전자"))
        self.assertEqual(stock_name_for(conn, "005930"), "삼성전자")
        self.assertIsNone(stock_name_for(conn, "000000"))
        conn.close()


if __name__ == "__main__":
    unittest.main()
