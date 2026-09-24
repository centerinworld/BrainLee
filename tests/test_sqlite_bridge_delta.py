from __future__ import annotations

import sqlite3
import unittest

from scripts.sync_sqlite_bridge_delta import nullable_conflict_columns


class SqliteBridgeSafetyTests(unittest.TestCase):
    def test_nullable_natural_key_is_detected(self) -> None:
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE TABLE sample (source_key TEXT, period TEXT)")
            conn.execute("INSERT INTO sample VALUES (NULL, '2026Q1')")
            self.assertEqual(
                nullable_conflict_columns(conn, "sample", ["source_key", "period"]),
                ["source_key"],
            )
        finally:
            conn.close()

    def test_complete_natural_key_is_allowed(self) -> None:
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE TABLE sample (source_key TEXT, period TEXT)")
            conn.execute("INSERT INTO sample VALUES ('A', '2026Q1')")
            self.assertEqual(
                nullable_conflict_columns(conn, "sample", ["source_key", "period"]),
                [],
            )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
