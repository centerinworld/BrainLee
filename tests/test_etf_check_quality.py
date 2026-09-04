import sqlite3
import unittest

from ETF_check.collector import (
    _append_unprocessed_failures,
    _find_missing_scope_codes,
    _scope_class_is_active,
)
from ETF_check.routes_etf import (
    _find_comparable_previous_date,
    _is_etf_change_anomaly,
    _is_stock_detail_page,
    _sanitize_scope_result,
    get_available_dates,
)


class EtfCheckQualityTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE collection_log (
                id INTEGER PRIMARY KEY,
                run_date TEXT,
                total_stocks INTEGER
            );
            CREATE TABLE etf_inclusion_daily (
                trade_date TEXT,
                stock_code TEXT,
                etf_amount REAL,
                market_cap REAL,
                mktcap_ratio REAL,
                etf_count INTEGER,
                scope_label TEXT,
                is_backfilled INTEGER DEFAULT 0
            );
            CREATE TABLE etf_stock_meta (
                stock_code TEXT PRIMARY KEY,
                secugrp_nm TEXT
            );
            INSERT INTO collection_log VALUES (1, '2026-08-20', 1000);
            """
        )

    def tearDown(self):
        self.conn.close()

    def _insert_day(self, trade_date, rows, count, scope="K-ETF", backfilled=False):
        self.conn.executemany(
            """
            INSERT INTO etf_inclusion_daily
            VALUES (?, ?, 100, 10000, 1, ?, ?, ?)
            """,
            [
                (trade_date, f"{idx:06d}", count, scope, int(backfilled))
                for idx in range(rows)
            ],
        )

    def test_dates_exclude_tiny_and_backfilled_snapshots(self):
        self._insert_day("2026-08-20", 400, 20)
        self._insert_day("2026-08-19", 299, 20)
        self._insert_day("2026-08-18", 400, 20, backfilled=True)

        self.assertEqual(get_available_dates(self.conn), ["2026-08-20"])

    def test_comparison_rejects_source_regime_change(self):
        self._insert_day("2026-08-20", 400, 20)
        self._insert_day("2026-08-19", 400, 40)

        previous, quality = _find_comparable_previous_date(
            self.conn, "2026-08-20", ["2026-08-19"]
        )

        self.assertIsNone(previous)
        self.assertTrue(quality["regime_break_detected"])

    def test_comparison_accepts_same_scope_and_overlap(self):
        self._insert_day("2026-08-20", 400, 20)
        self._insert_day("2026-08-19", 400, 20)

        previous, quality = _find_comparable_previous_date(
            self.conn, "2026-08-20", ["2026-08-19"]
        )

        self.assertEqual(previous, "2026-08-19")
        self.assertEqual(quality["overlap_ratio"], 1.0)

    def test_one_day_comparison_rejects_distant_snapshot(self):
        self._insert_day("2026-08-20", 400, 20)
        self._insert_day("2026-08-06", 400, 20)

        previous, quality = _find_comparable_previous_date(
            self.conn,
            "2026-08-20",
            ["2026-08-06"],
            max_gap_days=4,
        )

        self.assertIsNone(previous)
        self.assertTrue(quality["date_gap_rejected"])

    def test_large_ratio_with_etf_count_regime_jump_is_anomaly(self):
        is_bad, reason = _is_etf_change_anomaly(
            {
                "current_amount": 100,
                "prev_amount": 500,
                "amount_diff": -400,
                "market_cap": 10_000,
                "current_etf_count": 10,
                "prev_etf_count": 20,
            }
        )

        self.assertTrue(is_bad)
        self.assertIn("배율 급변", reason)

    def test_login_redirect_is_not_accepted_as_stock_detail(self):
        self.assertFalse(
            _is_stock_detail_page(
                "https://www.etfcheck.co.kr/?redirect=%2Fmobile%2FsearchPdf%2F005930",
                "ETF CHECK 로그인 회원가입 005930",
                "005930",
            )
        )

    def test_matching_stock_detail_page_is_accepted(self):
        self.assertTrue(
            _is_stock_detail_page(
                "https://www.etfcheck.co.kr/mobile/searchPdf/005930",
                "삼성전자 005930 현재가 75,000원 ETF 검색수",
                "005930",
            )
        )

    def test_k_etf_scope_drops_stale_us_amount_card(self):
        result = _sanitize_scope_result(
            {
                "top_ratio": {"name": "KODEX 200", "ratio": "31.2%"},
                "top_amount": {"name": "IEMG", "amount": "$112.68억"},
            },
            "K-ETF",
        )

        self.assertIsNone(result["top_amount"])
        self.assertEqual(result["top_ratio"]["name"], "KODEX 200")

    def test_scope_activation_rejects_inactive_dom_state(self):
        self.assertFalse(_scope_class_is_active("etp_tag K-ETF inactive clickable"))
        self.assertTrue(_scope_class_is_active("etp_tag K-ETF clickable"))

    def test_unprocessed_stocks_are_registered_without_duplicates(self):
        failed = ["000002"]
        stocks = [
            {"stock_code": "000001"},
            {"stock_code": "000002"},
            {"stock_code": "000003"},
        ]

        added = _append_unprocessed_failures(failed, stocks, 1)

        self.assertEqual(added, 1)
        self.assertEqual(failed, ["000002", "000003"])

    def test_missing_scope_codes_are_reconciled_from_full_universe(self):
        stocks = [{"stock_code": "000001"}, {"stock_code": "000002"}]

        self.assertEqual(_find_missing_scope_codes(stocks, {"000001"}), ["000002"])


if __name__ == "__main__":
    unittest.main()
