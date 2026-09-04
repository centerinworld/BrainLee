import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ETF_check"))

from daily_rebalance_audit import initialize  # noqa: E402
from daily_rebalance_audit_v5 import classify_rescaling  # noqa: E402
from full_pdf_collector import connect  # noqa: E402


class BasketRescaleTest(unittest.TestCase):
    def test_uniform_scale_is_reclassified_but_outlier_remains(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(Path(directory) / "etf.db")
            initialize(conn)
            events = []
            for index in range(5):
                events.append(("20260901","20260828","100001",str(index),"X","shares_changed",100,101,1,10,10,0))
            events.append(("20260901","20260828","100001","outlier","Y","shares_changed",100,130,30,10,10,0))
            conn.executemany("INSERT INTO etf_pdf_rebalance_event VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",events)
            conn.commit()
            self.assertEqual(classify_rescaling(conn,"20260901"),5)
            counts = dict(conn.execute("SELECT change_type,COUNT(*) FROM etf_pdf_rebalance_event GROUP BY change_type"))
            self.assertEqual(counts["basket_rescale"],5)
            self.assertEqual(counts["shares_changed"],1)
            conn.close()


if __name__ == "__main__":
    unittest.main()
