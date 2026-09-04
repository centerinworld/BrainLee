import unittest
import sqlite3

from collectors.dart_backlog_collector import (
    _extract_backlog,
    _refresh_order_backlog_projection,
    _upsert_backlog_trigger,
)


class DartBacklogParserTest(unittest.TestCase):
    def test_rejects_derivative_contract_balance(self):
        text = """
        파생상품계약 현황 (단위: 천원) 구분 계약잔액 거래상대방 계약일 만기일
        선물환 USD 3,200,000 국민은행 2025-06-26 2025-09-26 현금흐름위험회피목적
        """
        self.assertIsNone(_extract_backlog(text).backlog_amount_krw)


    def test_rejects_contract_asset_balance(self):
        text = """
        계약자산 및 계약부채의 내역 (단위: 천원) 구분 당반기말 전기말
        매출채권 계약잔액 49,478,847 42,112,900 계약부채 12,000 13,000
        """
        self.assertIsNone(_extract_backlog(text).backlog_amount_krw)


    def test_parses_eok_shorthand_as_eok_won(self):
        text = "보고서 제출일 현재 당사의 수주잔고는 약 1,324억 입니다."
        result = _extract_backlog(text)
        self.assertEqual(result.backlog_unit, "억원")
        self.assertEqual(result.backlog_amount_krw, 132_400_000_000)


    def test_nearest_unit_declaration_wins(self):
        text = """
        (단위 : 백만원) 무관한 표 1,000 2,000 3,000
        기타 설명이 이어집니다. (단위 : 천원) 당기 기초 계약잔액 10 신규계약 20
        수익인식 50,000 기말 계약잔액 250,000
        """
        result = _extract_backlog(text)
        self.assertEqual(result.backlog_unit, "천원")
        self.assertEqual(result.backlog_amount_krw, 250_000_000)

    def test_normalizes_malformed_million_unit(self):
        text = """
        수주현황 (단위 : 백만) 품목 수주총액 기납품액 수주잔고
        수량 금액 수량 금액 수량 금액 서비스 10 20 5 10 5 10
        합 계 10 20 5 10 5 10 주석) 기준일 현재
        """
        result = _extract_backlog(text)
        self.assertEqual(result.backlog_unit, "백만원")
        self.assertEqual(result.backlog_amount_krw, 10_000_000)

    def test_currency_unit_can_precede_quantity_unit(self):
        text = """
        수주현황 (단위 : 백만원, EA) 품목 수주총액 기납품액 수주잔고
        수량 금액 수량 금액 수량 금액 장비 10 20 5 10 5 10
        합 계 10 20 5 10 5 10 주석) 기준일 현재
        """
        result = _extract_backlog(text)
        self.assertEqual(result.backlog_unit, "백만원")


    def test_keeps_opening_closing_order_backlog_table(self):
        text = """
        당기말 장비수주 계약잔액 (단위 : 천원)
        구분 기초 신규계약 수익인식 기말 반도체장비 84,406,992 309,317,594
        298,079,051 95,645,534 (2) 전기말 장비수주 계약잔액
        """
        result = _extract_backlog(text)
        self.assertEqual(result.backlog_amount_krw, 95_645_534_000)

    def test_rejects_unfinished_construction_loss_provision(self):
        text = """
        미완성공사의 손실예상액에 대한 공사손실충당부채의 변동내역은 다음과 같습니다.
        (단위: 원) 당3분기 기초잔액 증감액 당3분기말 잔액
        계속사업 2,665,909,990 1,798,802,246 4,464,712,236
        """
        self.assertIsNone(_extract_backlog(text).backlog_amount_krw)

    def test_rejects_negative_backlog_candidate(self):
        text = "수주잔고 (단위 : 백만원) 합계 -1,000"
        self.assertIsNone(_extract_backlog(text).backlog_amount_krw)

    def test_rejects_year_after_unit_conversion(self):
        text = "수주잔고 (단위 : 백만원) 납기 2021"
        self.assertIsNone(_extract_backlog(text).backlog_amount_krw)

    def test_rejects_decimal_date_after_unit_conversion(self):
        text = "수주잔고 (단위 : 백만원) 수주일자 2020.11"
        self.assertIsNone(_extract_backlog(text).backlog_amount_krw)

    def test_rejects_generic_table_first_number_fallback(self):
        text = "수주잔고 (단위 : 백만원) 품목 수주일자 납기 장비 5. 위험관리"
        self.assertIsNone(_extract_backlog(text).backlog_amount_krw)

    def test_quantity_amount_table_uses_total_backlog_amount(self):
        text = """
        수주현황 (단위 : EA, 백만원) 구분 전기말 수주잔고 당반기 수주현황
        당반기 납품현황 당반기말 수주잔고 수량 금액 수량 금액 수량 금액 수량 금액
        엔진부품 121,477 9,112 58,360 3,311 90,375 5,933 89,462 6,490
        합 계 138,998 61,496 103,938 61,132 117,073 40,916 125,863 81,712
        주) 환율을 적용하였습니다.
        """
        result = _extract_backlog(text)
        self.assertEqual(result.backlog_unit, "백만원")
        self.assertEqual(result.backlog_amount_krw, 81_712_000_000)

    def test_quantity_amount_table_without_unit_is_review_only(self):
        text = """
        수주현황 (단위 :) 구분 전기말 수주잔고 당반기 수주현황
        당반기 납품현황 당반기말 수주잔고 수량 금액 수량 금액 수량 금액 수량 금액
        제품 100,000 200,000 300,000 400,000 500,000 600,000 700,000 800,000
        합 계 100,000 200,000 300,000 400,000 500,000 600,000 700,000 800,000
        주) 단위 표기가 누락되었습니다.
        """
        result = _extract_backlog(text)
        self.assertEqual(result.backlog_confidence, 0.55)

    def test_quantity_table_does_not_take_total_from_next_section(self):
        text = """
        수주상황 (단위 : 톤, 억원) 품목 수주총액 기납품액 수주잔고
        수량 금액 수량 금액 수량 금액 강관제품 56,285 561 37,300 344 18,985 216
        [주요 종속회사의 내용] 다른 표 (단위 : 백만원)
        품목 수주총액 기납품액 수주잔고 수량 금액 수량 금액 수량 금액
        동합금 1,423 16,139 1,223 13,871 200 2,268
        6. 시장위험과 위험관리 환율 합계 628,165,200
        """
        result = _extract_backlog(text)
        self.assertLess(result.backlog_confidence, 0.95)
        self.assertNotEqual(result.backlog_amount_krw, 628_165_200 * 100_000_000)

    def test_quantity_table_stops_at_next_unit_declaration(self):
        text = """
        수주상황 (단위 : 백만원) 품목 수주총액 기납품액 수주잔고
        수량 금액 수량 금액 수량 금액 풍력 - 205,550 - 131,036 - 74,514
        건설 - 122,877 - 44,009 - 78,868 합 계 - 328,427 - 175,045 - 153,382
        설명 이후 별도기준 (단위 : 천원) 구분 수주총액 미청구공사 공사미수금
        합 계 - 9,157,347 - 28,758,640 23,935,872
        """
        result = _extract_backlog(text)
        self.assertEqual(result.backlog_amount_krw, 153_382_000_000)

    def test_opening_closing_table_uses_total_closing_balance(self):
        text = """
        계약 잔액의 변동내역은 다음과 같습니다. (단위: 백만원)
        구분 기초잔액 증감액 공사수익인식 기말잔액
        건설 6,213,164 1,777,310 1,254,577 6,735,897
        중공업 470,374 228,765 258,703 440,436
        합계 6,683,538 2,006,075 1,513,280 7,176,333
        (주1) 도급감소액은 647,384백만원입니다.
        """
        result = _extract_backlog(text)
        self.assertEqual(result.backlog_amount_krw, 7_176_333_000_000)

    def test_long_won_table_does_not_truncate_total_number(self):
        text = """
        계약 잔액 변동은 다음과 같습니다. (단위: 원)
        구분 기초 공사계약잔액 증감액 전기공사수익 기말 공사계약잔액
        계속사업 72,611,775,080 109,432,413,500 43,600,678,315 138,443,510,265
        중단사업 1,744,567,293 (299,087,042) 1,357,971,637 87,508,614
        합 계 74,356,342,373 109,133,326,458 44,958,649,952 138,531,018,879
        (전반기) (단위: 원) 구분 기초 증감 수익 기말
        """
        result = _extract_backlog(text)
        self.assertEqual(result.backlog_amount_krw, 138_531_018_879)


class BacklogQualityGateTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript("""
            CREATE TABLE dart_backlog_quarterly (
                stock_code TEXT, fiscal_year INTEGER, fiscal_quarter INTEGER,
                report_type TEXT, backlog_amount_krw REAL, backlog_confidence REAL
            );
            CREATE TABLE order_backlog (
                stock_code TEXT, year INTEGER, quarter INTEGER,
                backlog_amount REAL, backlog_unit TEXT, backlog_normalized REAL,
                backlog_to_rev REAL, collected_at TEXT
            );
            CREATE TABLE dart_tenbagger_triggers_quarterly (
                stock_code TEXT, fiscal_year INTEGER, fiscal_quarter INTEGER,
                report_type TEXT, metric_name TEXT, metric_value REAL, yoy_pct REAL,
                qoq_pct REAL, trigger_level TEXT, source_table TEXT, updated_at TEXT,
                UNIQUE(stock_code,fiscal_year,fiscal_quarter,report_type,metric_name)
            );
        """)

    def tearDown(self):
        self.conn.close()

    def test_twenty_fold_pair_is_removed_from_projection_and_triggers(self):
        rows = [(2025, 1, 100.0, 0.9), (2025, 2, 2_100.0, 0.9)]
        for year, quarter, amount, confidence in rows:
            self.conn.execute(
                "INSERT INTO dart_backlog_quarterly VALUES('000001',?,?, 'CFS',?,?)",
                (year, quarter, amount, confidence),
            )
            self.conn.execute(
                "INSERT INTO order_backlog VALUES('000001',?,?,?,'원',?,NULL,NULL)",
                (year, quarter, amount, amount / 1_000_000),
            )
        counts = _refresh_order_backlog_projection(self.conn, "000001")
        self.assertEqual(counts, {"accepted": 0, "rejected": 2})
        for year, quarter, _, _ in rows:
            _upsert_backlog_trigger(self.conn, "000001", year, quarter, "CFS")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM dart_tenbagger_triggers_quarterly").fetchone()[0],
            0,
        )

    def test_low_confidence_neighbor_does_not_invalidate_good_source_row(self):
        rows = [(2025, 1, 100.0, 0.96), (2025, 2, 10_000.0, 0.6)]
        for year, quarter, amount, confidence in rows:
            self.conn.execute(
                "INSERT INTO dart_backlog_quarterly VALUES('000001',?,?, 'CFS',?,?)",
                (year, quarter, amount, confidence),
            )
            self.conn.execute(
                "INSERT INTO order_backlog VALUES('000001',?,?,?,'원',?,NULL,NULL)",
                (year, quarter, amount, amount / 1_000_000),
            )
        counts = _refresh_order_backlog_projection(self.conn, "000001")
        self.assertEqual(counts, {"accepted": 1, "rejected": 1})


if __name__ == "__main__":
    unittest.main()
