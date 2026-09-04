import importlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"ETF_check"))
routes_etf=importlib.import_module("routes_etf")


class SourceRouterCutoverTest(unittest.TestCase):
    def test_package_wrapper_is_loaded(self):
        self.assertTrue(str(routes_etf.__file__).endswith("routes_etf/__init__.py"))

    def test_direct_source_is_used_only_in_primary_mode(self):
        with patch("etf_primary_service.source_mode",return_value="krx_primary"), patch(
            "etf_primary_service.direct_summary",return_value={"source":"KRX_KIS_DIRECT"}
        ):
            self.assertEqual(routes_etf.get_etf_list("005930")["source"],"KRX_KIS_DIRECT")

    def test_invalid_code_is_rejected_before_source_lookup(self):
        with self.assertRaises(Exception):
            routes_etf.get_etf_list("ABC")


if __name__=="__main__": unittest.main()
