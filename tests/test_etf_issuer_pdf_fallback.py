import sys
import unittest
from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"ETF_check"))

from issuer_pdf_fallback import parse_plus  # noqa: E402


class IssuerFallbackTest(unittest.TestCase):
    def test_effective_date_is_preserved(self):
        payload={"totalElements":1,"content":[{"wkdate":"20260826","krJmCd":"US1","jmNm":"X","amount":2,"ratio":3}]}
        effective,rows=parse_plus(payload)
        self.assertEqual(effective,"20260826")
        self.assertEqual(rows[0]["code"],"US1")

    def test_incomplete_pagination_is_rejected(self):
        payload={"totalElements":2,"content":[{"wkdate":"20260826"}]}
        with self.assertRaises(RuntimeError):
            parse_plus(payload)


if __name__=="__main__":
    unittest.main()
