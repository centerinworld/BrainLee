from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import collection_health
from collection_health import DatasetContract, evaluate_contract


class DatasetContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "health.db"
        conn = sqlite3.connect(self.db_path)
        conn.execute("CREATE TABLE prices(stock_code TEXT,date TEXT,updated_at TEXT)")
        conn.executemany(
            "INSERT INTO prices VALUES(?,?,?)",
            [
                ("000001", "2026-07-16", "2026-07-16 18:00:00"),
                ("000002", "2026-07-16", "2026-07-16 18:00:00"),
                ("000003", "2026-07-16", "2026-07-16 18:00:00"),
                ("000001", "2026-07-17", "2026-07-17 09:05:00"),
            ],
        )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_intraday_partial_rows_do_not_hide_complete_previous_close(self) -> None:
        contract = DatasetContract(
            "prices", "prices", self.db_path, "prices", "date",
            ready_hour=16, min_latest_coverage=3,
            coverage_expr="COUNT(DISTINCT stock_code)", collected_at_col="updated_at",
        )
        result = evaluate_contract(contract, now=datetime(2026, 7, 17, 10, 0))
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["source_as_of"], "2026-07-17")
        self.assertEqual(result["coverage_as_of"], "2026-07-16")
        self.assertEqual(result["latest_coverage"], 3)

    def test_daily_contract_detects_trading_day_lag(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM prices WHERE date>'2026-07-14'")
        conn.execute("INSERT INTO prices VALUES('000001','2026-07-14','2026-07-14 18:00:00')")
        conn.commit()
        conn.close()
        contract = DatasetContract(
            "prices", "prices", self.db_path, "prices", "date",
            ready_hour=16, min_latest_coverage=1,
            coverage_expr="COUNT(DISTINCT stock_code)",
        )
        result = evaluate_contract(contract, now=datetime(2026, 7, 17, 10, 0))
        self.assertEqual(result["status"], "stale")
        self.assertGreaterEqual(result["lag"], 2)

    def test_etf_contract_uses_positive_rows_and_run_status(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            CREATE TABLE etf_inclusion_daily(
                trade_date TEXT,
                stock_code TEXT,
                etf_amount REAL,
                etf_count INTEGER,
                collected_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE collection_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date TEXT,
                started_at TEXT,
                finished_at TEXT,
                total_stocks INTEGER,
                success INTEGER,
                failed INTEGER,
                status TEXT
            )
            """
        )
        rows = [( "2026-07-17", f"{i:06d}", 100.0, 1, "2026-07-17 20:40:00") for i in range(520)]
        rows.extend([( "2026-07-17", f"9{i:05d}", 0.0, 0, "2026-07-17 20:40:00") for i in range(10)])
        conn.executemany("INSERT INTO etf_inclusion_daily VALUES(?,?,?,?,?)", rows)
        conn.execute(
            "INSERT INTO collection_log(run_date,started_at,finished_at,total_stocks,success,failed,status) VALUES(?,?,?,?,?,?,?)",
            ("2026-07-17", "2026-07-17 20:30:00", "2026-07-17 21:20:00", 530, 520, 10, "done"),
        )
        conn.commit()
        conn.close()

        contract = DatasetContract(
            "etf", "ETF", self.db_path, "etf_inclusion_daily", "trade_date",
            ready_hour=19, min_latest_coverage=500,
            coverage_expr="SUM(CASE WHEN COALESCE(etf_amount, 0) > 0 THEN 1 ELSE 0 END)",
        )
        result = evaluate_contract(contract, now=datetime(2026, 7, 17, 22, 0))
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["latest_coverage"], 520)
        self.assertEqual(result["positive_rows"], 520)
        self.assertEqual(result["run_status"], "done")


class CollectionLedgerTests(unittest.TestCase):
    def test_run_lifecycle_is_recorded_in_sidecar_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "collection_health.db"
            with patch.object(collection_health, "LEDGER_DB", ledger):
                run_id = collection_health.start_collection_run("test-job")
                collection_health.finish_collection_run(
                    run_id, "success", details={"saved_rows": 12}
                )
                rows = collection_health.latest_collection_runs()
        self.assertEqual(rows[0]["job_name"], "test-job")
        self.assertEqual(rows[0]["status"], "success")
        self.assertEqual(rows[0]["details"]["saved_rows"], 12)

    def test_restart_marks_unfinished_runs_as_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "collection_health.db"
            with patch.object(collection_health, "LEDGER_DB", ledger):
                collection_health.start_collection_run("unfinished-job")
                changed = collection_health.interrupt_running_collection_runs()
                rows = collection_health.latest_collection_runs()
        self.assertEqual(changed, 1)
        self.assertEqual(rows[0]["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()
