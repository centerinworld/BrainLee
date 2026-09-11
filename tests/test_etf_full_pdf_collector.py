import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ETF_check"))

from full_pdf_collector import (  # noqa: E402
    ETF,
    assess_and_publish,
    connect,
    membership,
    normalized,
    response_quality_issue,
    save_failure,
    save_snapshot,
)


ROWS = [
    {
        "COMPST_ISU_CD": "172670",
        "COMPST_ISU_NM": "에이엘티",
        "COMPST_ISU_CU1_SHRS": "1,200.00",
        "VALU_AMT": "12,000,000",
        "COMPST_AMT": "12,100,000",
        "COMPST_RTO": "1.25",
    },
    {
        "COMPST_ISU_CD": "CASH",
        "COMPST_ISU_NM": "원화현금",
        "COMPST_ISU_CU1_SHRS": "1",
        "VALU_AMT": "100,000",
        "COMPST_AMT": "100,000",
        "COMPST_RTO": "0.01",
    },
]


class FullPDFCollectorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_normalization_keeps_non_stock_components(self):
        items = normalized(ROWS)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["is_domestic"], 1)
        self.assertEqual(items[1]["code"], "CASH")
        self.assertEqual(items[1]["is_domestic"], 0)

    def test_normalization_recovers_six_digit_code_from_isin_map(self):
        rows = [{
            "COMPST_ISU_CD": "KR7005830005",
            "COMPST_ISU_CD2": "KR7005830005",
            "COMPST_ISU_NM": "DB손해보험",
        }]
        item = normalized(rows, {"KR7005830005": "005830"})[0]
        self.assertEqual(item["code"], "005830")
        self.assertEqual(item["is_domestic"], 1)

    def test_truncated_unweighted_response_is_rejected(self):
        rows = [
            {"COMPST_ISU_CD": "005930", "COMPST_RTO": "-"}
            for _ in range(3)
        ]
        self.assertIsNotNone(response_quality_issue(rows, previous_count=34))
        rows[0]["COMPST_RTO"] = "33.3"
        self.assertIsNotNone(response_quality_issue(rows, previous_count=34))
        for row in rows:
            row["COMPST_RTO"] = "33.4"
        self.assertIsNone(response_quality_issue(rows, previous_count=34))

    def test_partial_date_cannot_confirm_absence(self):
        self.conn.execute(
            "INSERT INTO etf_pdf_full_publication VALUES(?,?,?,?,?,?)",
            ("20260827",1,1,1,"now","test"),
        )
        result = membership(self.conn,"172670","20260828")
        self.assertEqual(result["verdict"],"snapshot_incomplete")
        self.assertFalse(result["is_confirmed"])

    def test_complete_date_confirms_positive_and_zero(self):
        etf=ETF("069500","KODEX 200","KR7069500007")
        save_snapshot(self.conn,"20260828",etf,ROWS,"raw.gz","abc")
        assessment=assess_and_publish(self.conn,"20260828",1)
        self.assertTrue(assessment["complete"])
        self.assertEqual(membership(self.conn,"172670","20260828")["verdict"],"included")
        self.assertEqual(
            membership(self.conn,"005930","20260828")["verdict"],
            "confirmed_not_included",
        )

    def test_missing_etf_prevents_publication(self):
        etf=ETF("069500","KODEX 200","KR7069500007")
        save_snapshot(self.conn,"20260828",etf,ROWS,"raw.gz","abc")
        assessment=assess_and_publish(self.conn,"20260828",2)
        self.assertFalse(assessment["complete"])

    def test_failure_clears_components_and_revokes_publication(self):
        etf=ETF("069500","KODEX 200","KR7069500007")
        save_snapshot(self.conn,"20260828",etf,ROWS,"raw.gz","abc")
        self.assertTrue(assess_and_publish(self.conn,"20260828",1)["complete"])
        save_failure(self.conn,"20260828",etf,"error","truncated")
        assessment=assess_and_publish(self.conn,"20260828",1)
        self.assertFalse(assessment["complete"])
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM etf_pdf_full_component WHERE base_date='20260828'"
            ).fetchone()[0],
            0,
        )
        self.assertIsNone(
            self.conn.execute(
                "SELECT 1 FROM etf_pdf_full_publication WHERE base_date='20260828'"
            ).fetchone()
        )


if __name__ == "__main__":
    unittest.main()
