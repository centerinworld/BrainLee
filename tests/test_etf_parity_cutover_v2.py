import sqlite3,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"ETF_check"))
from etf_parity_cutover import initialize

class CutoverStateTest(unittest.TestCase):
    def test_initial_state_never_starts_as_primary(self):
        with tempfile.TemporaryDirectory() as d:
            c=sqlite3.connect(Path(d)/"x.db");initialize(c)
            row=c.execute("SELECT mode,required_pass_days,consecutive_pass_days FROM etf_source_control").fetchone()
            self.assertEqual(row,("legacy_validation",5,0));c.close()

if __name__=="__main__":unittest.main()
