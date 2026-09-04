import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ETF_check"))

from daily_rebalance_audit_v3 import audit_day  # noqa: E402
from etf_universe_sync import initialize as initialize_universe  # noqa: E402
from full_pdf_collector import connect  # noqa: E402


class NullSafeRebalanceAuditTest(unittest.TestCase):
    def test_null_quantities_are_compared_without_failure(self):
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
                    ("20260828","100001","A","KR7100001000","success",1,"now"),
                    ("20260901","100001","A","KR7100001000","success",1,"now"),
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
                    ("20260828","100001",1,"CASH","Cash",None,None,0,"{}"),
                    ("20260901","100001",1,"CASH","Cash",None,None,0,"{}"),
                ],
            )
            conn.commit()
            result = audit_day(conn,"20260901")
            self.assertEqual(result["event_count"],0)
            conn.close()


if __name__ == "__main__":
    unittest.main()
