import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

import employment_monitor.routes_employment_v2 as routes
import employment_monitor.collect_nps_monthly as nps_collector
from employment_monitor.data_quality import (
    DEFAULT_EMP_DB,
    DEFAULT_STOCK_DB,
    _month_lag,
    audit_employment_data,
)


class EmploymentDataQualityTest(unittest.TestCase):
    def test_insurance_scope_is_context_not_invalidation(self):
        profile = routes._employment_scope_profile("삼성생명보험", "보험")
        self.assertEqual(profile["type"], "insurance_sales_network")
        self.assertIn("컨설턴트", profile["note"])
        self.assertNotIn("무효", profile["note"])

    def test_month_lag(self):
        self.assertEqual(_month_lag("202606", date(2026, 8, 23)), 2)
        self.assertIsNone(_month_lag("202613", date(2026, 8, 23)))

    def test_current_database_has_no_integrity_errors(self):
        report = audit_employment_data(DEFAULT_EMP_DB, DEFAULT_STOCK_DB)
        self.assertEqual(report["integrity"], ["ok"])
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["checks"]["nps_net_mismatch"], 0)
        self.assertEqual(report["checks"]["nps_duplicate_keys"], 0)
        self.assertEqual(report["checks"]["wlb_duplicate_keys"], 0)
        self.assertIn("nps_portal", report["history"])
        self.assertIn("wlb_portal", report["history"])

    def test_routes_use_common_maximum_month(self):
        with tempfile.TemporaryDirectory() as tmp:
            emp_db = Path(tmp) / "employment.db"
            stock_db = Path(tmp) / "stock.db"
            stock = sqlite3.connect(stock_db)
            stock.execute(
                "CREATE TABLE stock_universe (stock_code TEXT, stock_name TEXT, market TEXT, sector_small TEXT, secugrp_nm TEXT)"
            )
            stock.executemany(
                "INSERT INTO stock_universe VALUES (?,?,?,?,?)",
                [("000001", "최신", "KOSPI", "테스트", "주권"), ("000002", "지연", "KOSDAQ", "테스트", "주권")],
            )
            stock.commit()
            stock.close()

            emp = sqlite3.connect(emp_db)
            emp.executescript(
                """
                CREATE TABLE wlb_monthly (
                    data_ym TEXT, stock_code TEXT, stock_name TEXT,
                    total_workers INTEGER, workplace_cnt INTEGER, fetched_at TEXT
                );
                CREATE TABLE nps_monthly (
                    data_ym TEXT, stock_code TEXT, new_hires INTEGER,
                    terminations INTEGER, net_change INTEGER, wkpl_count INTEGER
                );
                CREATE TABLE employment_company (
                    stock_code TEXT, ym TEXT, worker_count INTEGER, source TEXT, bizr_no TEXT
                );
                CREATE TABLE stock_bizr_no_map (stock_code TEXT, bizr_no TEXT);
                CREATE TABLE stock_bizno_map (stock_code TEXT, biz_no_6 TEXT);
                CREATE TABLE wlb_portal_annual (
                    stock_code TEXT, data_year TEXT, total_workers INTEGER,
                    workplace_count INTEGER, source_rows INTEGER, identity_matched_rows INTEGER
                );
                """
            )
            emp.executemany(
                "INSERT INTO wlb_monthly VALUES (?,?,?,?,?,?)",
                [
                    ("202608", "000001", "최신", 100, 1, "2026-08-01"),
                    ("202608", "000002", "지연", 50, 1, "2026-08-01"),
                    ("202607", "000001", "최신", 90, 1, "2026-08-22"),
                ],
            )
            emp.executemany(
                "INSERT INTO nps_monthly VALUES (?,?,?,?,?,?)",
                [("202606", "000001", 10, 3, 7, 1), ("202605", "000002", 8, 2, 6, 1)],
            )
            emp.executemany(
                "INSERT INTO stock_bizno_map VALUES (?,?)", [("000001", "111111"), ("000002", "222222")]
            )
            emp.execute("INSERT INTO wlb_portal_annual VALUES ('000001','2024',95,2,2,0)")
            emp.commit()
            emp.close()

            old_emp, old_stock = routes.EMP_DB, routes.STOCK_DB
            try:
                routes.EMP_DB, routes.STOCK_DB = str(emp_db), str(stock_db)
                routes._trend_data_cache = None
                yearly = routes.get_yearly_employment()
                trend = routes.get_nps_trend(limit=10)
                annual = routes.get_annual_trend("000001")
            finally:
                routes.EMP_DB, routes.STOCK_DB = old_emp, old_stock
                routes._trend_data_cache = None

            self.assertEqual(yearly["data_ym"], "202608")
            self.assertEqual(yearly["returned_count"], 2)
            by_code = {row["stock_code"]: row for row in trend["rows"]}
            self.assertEqual(by_code["000001"]["nps_ref_ym"], "202606")
            self.assertIsNone(by_code["000002"]["display_diff_1m"])
            self.assertEqual(annual["results"][0]["official_history"][0]["worker_count"], 95)
            self.assertEqual(annual["results"][0]["official_history"][0]["match_quality"], "exact_biz_no")
            self.assertTrue(annual["results"][0]["official_history"][0]["data_valid"])
            self.assertEqual(annual["results"][0]["employment_scope"]["type"], "general")

    def test_nps_bonus_uses_distinct_recent_months(self):
        with tempfile.TemporaryDirectory() as tmp:
            emp_db = Path(tmp) / "employment.db"
            conn = sqlite3.connect(emp_db)
            conn.execute(
                "CREATE TABLE nps_monthly (stock_code TEXT, data_ym TEXT, net_change INTEGER)"
            )
            conn.executemany(
                "INSERT INTO nps_monthly VALUES (?,?,?)",
                [("000001", "202604", 40), ("000001", "202605", 40), ("000001", "202606", 40)],
            )
            conn.commit()
            conn.close()
            old_db = nps_collector.EMP_DB
            try:
                nps_collector.EMP_DB = str(emp_db)
                bonus = nps_collector.get_nps_bonus_map(months=3)
            finally:
                nps_collector.EMP_DB = old_db
            self.assertEqual(bonus["000001"], 2)


if __name__ == "__main__":
    unittest.main()
