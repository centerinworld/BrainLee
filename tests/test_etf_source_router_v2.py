import importlib.util,sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"ETF_check"))
SPEC=importlib.util.spec_from_file_location("routes_etf_active_test",ROOT/"ETF_check/routes_etf/__init__.py");m=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(m)
class RouterV2Test(unittest.TestCase):
 def test_primary_uses_direct(self):
  with patch("etf_primary_service.source_mode",return_value="krx_primary"),patch("etf_primary_service.direct_summary",return_value={"source":"KRX_KIS_DIRECT"}):self.assertEqual(m.get_etf_list("005930")["source"],"KRX_KIS_DIRECT")
 def test_validation_mode_uses_last_validated_direct_without_external_call(self):
  direct={"source":"KRX_KIS_DIRECT","note":"direct"}
  with patch("etf_primary_service.source_mode",return_value="legacy_validation"),patch("etf_primary_service.direct_summary",return_value=direct):
   result=m.get_etf_list("005930")
  self.assertEqual(result["source"],"KRX_KIS_LAST_VALIDATED")
  self.assertIn("품질 게이트",result["note"])
if __name__=="__main__":unittest.main()
