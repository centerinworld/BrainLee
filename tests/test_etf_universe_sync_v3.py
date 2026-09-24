import io,sys,unittest,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/"ETF_check"))
from direct_etf_pipeline import NAMES,WIDTHS
from etf_universe_sync_v3 import parse_master_zip

class CompleteUniverseTest(unittest.TestCase):
    def test_parser_includes_alphanumeric_etfs(self):
        def master_line(ticker,name,isin):
            values={key:"" for key in NAMES}
            values.update(group="EF",listed_date="20260901",listed_shares_thousand="123")
            tail=b"".join(
                str(values[key]).encode("cp949").ljust(width,b" ")[:width]
                for key,width in zip(NAMES,WIDTHS)
            )
            head=("000"+ticker+isin+name).encode("cp949")
            return head+tail
        raw=b"\n".join([
            master_line("069500","KODEX 200","KR7069500007"),
            master_line("0194M0","영문코드 ETF","KR70194M0001"),
        ])
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,"w") as archive:
            archive.writestr("kospi_code.mst",raw)
        rows=parse_master_zip(buffer.getvalue(),minimum_count=2)
        codes={row.ticker for row in rows}
        self.assertEqual(len(rows),2)
        self.assertIn("0194M0",codes)
        self.assertTrue(any(not code.isdigit() for code in codes))

if __name__=="__main__": unittest.main()
