import unittest

from employment_monitor.backfill_wlb_portal_history import _identity, _indexes, _integer, _year_from_title


class WlbPortalHistoryTest(unittest.TestCase):
    def test_recognizes_portal_headers(self):
        self.assertEqual(
            _indexes(["사업자등록번호", "고용보험 상시근로자수", "사업장명", "사업장 주소"]),
            {"biz": 0, "workers": 1, "name": 2, "address": 3},
        )

    def test_parses_integer(self):
        self.assertEqual(_integer("1,234"), 1234)
        self.assertEqual(_integer(None), 0)

    def test_accepts_only_year_end_archives(self):
        self.assertEqual(_year_from_title("가입 현황_20151231"), "2015")
        self.assertIsNone(_year_from_title("가입 사업장 정보(2019.6.)"))

    def test_workplace_identity_normalizes_spacing(self):
        self.assertEqual(_identity("삼성 전자(주)", "경기 수원시 1"), _identity("삼성전자(주)", "경기수원시1"))


if __name__ == "__main__":
    unittest.main()
