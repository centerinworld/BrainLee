import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/"ETF_check"))
from etf_universe_sync_v3 import fetch_complete_universe

class CompleteUniverseTest(unittest.TestCase):
    def test_parser_includes_alphanumeric_etfs(self):
        rows=fetch_complete_universe(); codes={row.ticker for row in rows}
        self.assertGreaterEqual(len(rows),1100)
        self.assertIn("0194M0",codes)
        self.assertTrue(any(not code.isdigit() for code in codes))

if __name__=="__main__": unittest.main()
