import sqlite3
import unittest

from security_master import _kr_code_predicate


class SecurityMasterCodeFilterTest(unittest.TestCase):
    def test_sqlite_filter_accepts_alphanumeric_preferred_code(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE codes(stock_code TEXT)")
        conn.executemany(
            "INSERT INTO codes VALUES(?)",
            [("005930",), ("00088K",), ("12345",), ("ABC_12",), ("abcdef",)],
        )
        rows = conn.execute(
            f"SELECT stock_code FROM codes WHERE {_kr_code_predicate(conn)} ORDER BY stock_code"
        ).fetchall()
        self.assertEqual([row[0] for row in rows], ["00088K", "005930"])


if __name__ == "__main__":
    unittest.main()
