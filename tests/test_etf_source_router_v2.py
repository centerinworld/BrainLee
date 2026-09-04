import importlib.util,sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"ETF_check"))
SPEC=importlib.util.spec_from_file_location("routes_etf_v2_test",ROOT/"ETF_check/routes_etf_v2/__init__.py");m=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(m)
class RouterV2Test(unittest.TestCase):
 def test_primary_uses_direct(self):
  with patch("etf_primary_service.source_mode",return_value="krx_primary"),patch("etf_primary_service.direct_summary",return_value={"source":"KRX_KIS_DIRECT"}):self.assertEqual(m.get_etf_list("005930")["source"],"KRX_KIS_DIRECT")
 def test_validation_uses_k_only(self):
  with patch("etf_primary_service.source_mode",return_value="legacy_validation"),patch("etfcheck_k_service.fetch_summary",return_value={"source":"ETFCHECK_K_ONLY"}):self.assertEqual(m.get_etf_list("005930")["source"],"ETFCHECK_K_ONLY")
if __name__=="__main__":unittest.main()
