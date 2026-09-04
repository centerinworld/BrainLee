import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ETF_check"))

from full_pdf_collector_v2 import login_code_is_success  # noqa: E402


class KRXLoginCodeTest(unittest.TestCase):
    def test_cd001_is_success(self):
        self.assertTrue(login_code_is_success("CD001"))

    def test_empty_code_is_success(self):
        self.assertTrue(login_code_is_success(None))

    def test_password_change_is_not_success(self):
        self.assertFalse(login_code_is_success("CD010"))

    def test_bad_credentials_are_not_success(self):
        self.assertFalse(login_code_is_success("CD006"))


if __name__ == "__main__":
    unittest.main()
