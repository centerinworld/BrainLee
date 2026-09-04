import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ETF_check"))

from daily_rebalance_audit_v4 import audit_day  # noqa: E402
from etf_universe_sync import initialize as initialize_universe  # noqa: E402
from full_pdf_collector import connect  # noqa: E402


class StructuralRebalanceTest(unittest.TestCase):
    def test_weight_only_change_is_not_structural(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(Path(directory) / "etf.db")
            initialize_universe(conn)
            conn.execute(
                "INSERT INTO etf_universe_daily VALUES(?,?,?,?,?,?,?,?,?)",
                ("20260901","100001","A","KOSPI","KR7100001000","",None,"test","now"),
            )
            conn.executemany(
                """
                INSERT INTO etf_pdf_full_snapshot(
                    base_date,etf_ticker,etf_name,isin,status,component_count,collected_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                [
                    ("20260828","100001","A","KR7100001000","success",2,"now"),
                    ("20260901","100001","A","KR7100001000","success",3,"now"),
                ],
            )
            conn.executemany(
                """
                INSERT INTO etf_pdf_full_component(
                    base_date,etf_ticker,component_order,component_code,
                    component_name,shares_per_cu,weight,is_domestic_stock,raw_json
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                [
                    ("20260828","100001",1,"005930","Samsung",1,10,1,"{}"),
                    ("20260828","100001",2,"035420","Naver",1,10,1,"{}"),
                    ("20260901","100001",1,"005930","Samsung",1,12,1,"{}"),
                    ("20260901","100001",2,"035420","Naver",2,8,1,"{}"),
                    ("20260901","100001",3,"000660","SK",1,5,1,"{}"),
                ],
            )
            conn.commit()
            result = audit_day(conn,"20260901")
            self.assertEqual(result["valuation_drift"],1)
            self.assertEqual(result["shares_changed"],1)
            self.assertEqual(result["added"],1)
            self.assertEqual(result["structural_events"],2)
            conn.close()


if __name__ == "__main__":
    unittest.main()
