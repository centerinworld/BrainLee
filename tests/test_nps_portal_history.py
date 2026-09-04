import unittest

from employment_monitor.backfill_nps_portal_history import (
    _clean_name,
    _column_indexes,
    _extract_label_ym,
    _integer,
    match_stock_code,
)


class NpsPortalHistoryTest(unittest.TestCase):
    def test_extracts_historical_file_month(self):
        self.assertEqual(_extract_label_ym("국민연금 가입 사업장 내역 2015년 12월"), "201512")
        self.assertEqual(_extract_label_ym("국민연금공단_국민연금 가입 사업장 내역_20240119"), "202401")
        self.assertEqual(_extract_label_ym("국민연금공단_국민연금 가입 사업장 내역_10/22/2021"), "202110")

    def test_name_and_prefix_must_identify_one_stock(self):
        candidates = {"119810": {"000270", "999999"}}
        aliases = {"000270": {_clean_name("기아")}, "999999": {_clean_name("다른회사")}}
        self.assertEqual(
            match_stock_code("119810", "기아자동차(주)영업본부", candidates, aliases),
            ("000270", "prefix_name"),
        )
        self.assertEqual(match_stock_code("119810", "무관회사", candidates, aliases)[0], None)

    def test_recognizes_legacy_and_current_portal_headers(self):
        legacy = ["DATA_CRT_YM", "WKPL_NM", "BZOWR_RGST_NO", "JNNGP_CNT", "NW_ACQZR_CNT", "LSS_JNNGP_CNT"]
        current = ["자료생성년월", "사업장명", "사업자등록번호", "가입자수", "신규취득자수", "상실가입자수"]
        self.assertEqual(_column_indexes(legacy), _column_indexes(current))

    def test_parses_formatted_integer(self):
        self.assertEqual(_integer(" 12,345 "), 12345)
        self.assertEqual(_integer(""), 0)


if __name__ == "__main__":
    unittest.main()
