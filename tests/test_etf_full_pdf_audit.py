import sys
import tempfile
import unittest
from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"ETF_check"))

from full_pdf_audit import build_changes,health,initialize  # noqa: E402
from full_pdf_collector import ETF,assess_and_publish,connect,save_snapshot  # noqa: E402


def row(code,shares,weight):
    return {"COMPST_ISU_CD":code,"COMPST_ISU_NM":code,
            "COMPST_ISU_CU1_SHRS":str(shares),"VALU_AMT":"100",
            "COMPST_AMT":"100","COMPST_RTO":str(weight)}


class FullPDFAuditTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.conn=connect(Path(self.tmp.name)/"a.db")
        initialize(self.conn); self.etf=ETF("069500","KODEX 200","KR7069500007")

    def tearDown(self):
        self.conn.close(); self.tmp.cleanup()

    def test_change_types(self):
        save_snapshot(self.conn,"20260827",self.etf,[row("A",1,1),row("B",2,2)],"/tmp/a","a"*64)
        save_snapshot(self.conn,"20260828",self.etf,[row("A",3,1),row("C",4,4)],"/tmp/b","b"*64)
        self.assertEqual(build_changes(self.conn,"20260828","20260827"),3)
        kinds={r[0] for r in self.conn.execute("select change_type from etf_pdf_full_change")}
        self.assertEqual(kinds,{"added","removed","changed"})

    def test_unpublished_snapshot_is_not_healthy(self):
        save_snapshot(self.conn,"20260828",self.etf,[row("A",1,1)],"/tmp/missing","a"*64)
        self.assertEqual(health(self.conn,"20260828")["status"],"incomplete_or_invalid")


if __name__=="__main__":
    unittest.main()
