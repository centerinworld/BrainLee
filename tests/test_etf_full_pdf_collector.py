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


if __name__ == "__main__":
    unittest.main()
