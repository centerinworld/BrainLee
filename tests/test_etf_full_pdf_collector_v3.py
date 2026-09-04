import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ETF_check"))

import full_pdf_collector_v3 as v3  # noqa: E402


class KRXV3Test(unittest.TestCase):
    def test_v3_installs_current_session(self):
        self.assertIs(v3.collector.KRXSession, v3.CurrentKRXSession)

    def test_v3_keeps_success_code_semantics(self):
        self.assertTrue(v3.v2.login_code_is_success("CD001"))
        self.assertFalse(v3.v2.login_code_is_success("CD011"))


if __name__ == "__main__":
    unittest.main()
