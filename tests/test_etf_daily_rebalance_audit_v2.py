import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ETF_check"))

from daily_rebalance_audit_v2 import audit_day  # noqa: E402
from etf_universe_sync import initialize as initialize_universe  # noqa: E402
from full_pdf_collector import connect  # noqa: E402
from issuer_pdf_fallback import initialize as initialize_fallback  # noqa: E402


class DailyRebalanceAuditV2Test(unittest.TestCase):
    def test_healthy_fund_is_compared_when_another_fund_uses_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(Path(directory) / "etf.db")
            initialize_universe(conn)
            initialize_fallback(conn)
            conn.executemany(
                "INSERT INTO etf_universe_daily VALUES(?,?,?,?,?,?,?,?,?)",
                [
                    ("20260901","100001","A","KOSPI","KR7100001000","",None,"test","now"),
                    ("20260901","100002","B","KOSPI","KR7100002000","",None,"test","now"),
                ],
            )
            conn.executemany(
                """
                INSERT INTO etf_pdf_full_snapshot(
                    base_date,etf_ticker,etf_name,isin,status,component_count,collected_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                [
                    ("20260828","100001","A","KR7100001000","success",1,"now"),
                    ("20260901","100001","A","KR7100001000","success",2,"now"),
                    ("20260901","100002","B","KR7100002000","empty",0,"now"),
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
                    ("20260901","100001",1,"005930","Samsung",2,20,1,"{}"),
                    ("20260901","100001",2,"000660","SK",1,5,1,"{}"),
                ],
            )
            conn.execute(
                "INSERT INTO etf_pdf_issuer_fallback VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("20260901","100002","20260828","ISSUER","url","stale",1,"raw","hash","now"),
            )
            conn.commit()
            result = audit_day(conn,"20260901")
            self.assertEqual(result["compared_etfs"],1)
            self.assertEqual(result["unavailable_etfs"],1)
            self.assertEqual(result["added"],1)
            self.assertEqual(result["changed"],1)
            self.assertEqual(result["details"]["issuer_fallback"][0]["status"],"stale")
            conn.close()


if __name__ == "__main__":
    unittest.main()
