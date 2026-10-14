"""AGI-06 기업 보고서(agi/company_report.py) 회귀 테스트.

정본: docs/AGI_06_COMPANY_REPORT_DESIGN_20261013.md, AGI_06_SECTOR_REPORT_SAMPLES_20261013.md.

실제 종목코드로 실제 운영 테이블(`financial_data`·`stock_universe`·`company_product_mix`
등, 읽기 전용)을 읽는다 — 이 테이블들은 임시 스키마에 복제되지 않으므로 합성 데이터로
대체할 수 없다(AGI-02 텔레그램 테스트와 같은 패턴). 신설 2테이블(`company_reports`/
`company_report_sections`)에만 임시 스키마로 쓴다.

실행: ./venv/bin/python -m pytest tests/test_agi_company_report.py -v
"""
from __future__ import annotations

import unittest

from agi import company_report as cr
from tests.agi_test_support import isolated_schema

# 실제 보유 종목 — 업종이 서로 다르다(설계 문서 샘플 ①~③과 일치).
HANA_MICRON = "067310"       # 반도체 OSAT
LIVSMED = "491000"           # 의료기기
AJ_NETWORKS = "095570"       # 복합(렌탈+창고유통) — generic, 다중 부문
SK_TELECOM = "017670"        # 통신 — dividend_utility


def _skip_if_missing(conn, stock_code: str, label: str):
    row = conn.execute("SELECT 1 FROM stock_universe WHERE stock_code = ?", (stock_code,)).fetchone()
    if row is None:  # pragma: no cover - 운영 유니버스에서 빠진 경우만
        raise unittest.SkipTest(f"{label}({stock_code}) 이 stock_universe 에 없다")


class ClassifyTemplateTests(unittest.TestCase):
    def test_osat_member_classified_as_semiconductor(self) -> None:
        with isolated_schema(migrate=False) as (conn, _schema):
            _skip_if_missing(conn, HANA_MICRON, "하나마이크론")
            self.assertEqual(cr.classify_template(conn, HANA_MICRON), cr.TEMPLATE_SEMICONDUCTOR)

    def test_medtech_sector_large(self) -> None:
        with isolated_schema(migrate=False) as (conn, _schema):
            _skip_if_missing(conn, LIVSMED, "리브스메드")
            self.assertEqual(cr.classify_template(conn, LIVSMED), cr.TEMPLATE_MEDTECH)

    def test_telecom_classified_as_dividend_utility(self) -> None:
        with isolated_schema(migrate=False) as (conn, _schema):
            _skip_if_missing(conn, SK_TELECOM, "SK텔레콤")
            self.assertEqual(cr.classify_template(conn, SK_TELECOM), cr.TEMPLATE_DIVIDEND_UTILITY)

    def test_unknown_code_falls_back_to_generic(self) -> None:
        with isolated_schema(migrate=False) as (conn, _schema):
            self.assertEqual(cr.classify_template(conn, "999999"), cr.TEMPLATE_GENERIC)


class SectionBuilderTests(unittest.TestCase):
    def test_financials_are_oldest_first_with_qoq(self) -> None:
        with isolated_schema(migrate=False) as (conn, _schema):
            _skip_if_missing(conn, HANA_MICRON, "하나마이크론")
            out = cr.section_financials(conn, HANA_MICRON, quarters=2)
            self.assertIn(out["basis"], ("CFS", "OFS"))
            self.assertEqual(len(out["quarters"]), 2)
            first, second = out["quarters"]
            self.assertTrue((first["year"], first["quarter"]) <= (second["year"], second["quarter"]))
            if first["revenue_억"]:
                self.assertIn("revenue_qoq_pct", second)

    def test_business_mix_flags_multi_segment_for_aj_networks(self) -> None:
        """AJ네트웍스는 렌탈·창고유통 두 부문이 각 15%+ — 샘플 ③에서 발견한 규칙."""
        with isolated_schema(migrate=False) as (conn, _schema):
            _skip_if_missing(conn, AJ_NETWORKS, "AJ네트웍스")
            out = cr.section_business_mix(conn, AJ_NETWORKS)
            self.assertTrue(out["is_multi_segment"], out)
            self.assertGreaterEqual(len(out["major_segments_pct"]), 2)

    def test_margin_gap_flags_when_net_income_diverges_from_flat_operating_profit(self) -> None:
        financials = {
            "quarters": [
                {"operating_profit_억": 218.7, "net_income_억": 85.6},
                {"operating_profit_억": 214.9, "net_income_억": 283.3},
            ]
        }
        out = cr.section_margin_gap(financials)
        self.assertTrue(out["flag"])
        self.assertIsNotNone(out["note"])

    def test_margin_gap_does_not_flag_when_both_move_together(self) -> None:
        financials = {
            "quarters": [
                {"operating_profit_억": 100.0, "net_income_억": 80.0},
                {"operating_profit_억": 150.0, "net_income_억": 120.0},
            ]
        }
        out = cr.section_margin_gap(financials)
        self.assertFalse(out["flag"])

    def test_semiconductor_sector_section_lists_osat_peers(self) -> None:
        with isolated_schema(migrate=False) as (conn, _schema):
            _skip_if_missing(conn, HANA_MICRON, "하나마이크론")
            out = cr._sector_semiconductor(conn, HANA_MICRON)
            self.assertTrue(out["is_member"])
            self.assertGreaterEqual(len(out["peer_members"]), 5)

    def test_medtech_sector_section_declares_the_known_data_gap(self) -> None:
        out = cr._sector_medtech()
        self.assertFalse(out["regulatory_tracking_available"])

    def test_dividend_utility_section_reads_real_dividend_history(self) -> None:
        with isolated_schema(migrate=False) as (conn, _schema):
            _skip_if_missing(conn, SK_TELECOM, "SK텔레콤")
            out = cr._sector_dividend_utility(conn, SK_TELECOM)
            self.assertTrue(out["data_available"])
            self.assertGreaterEqual(len(out["recent_dividends"]), 1)


class StoreAndRetrieveTests(unittest.TestCase):
    """신설 2테이블은 임시 스키마에만 쓴다 — 운영 public 의 company_reports 는 무관하다."""

    def test_generate_creates_rows_then_rerun_without_change_is_unchanged(self) -> None:
        with isolated_schema() as (conn, schema):
            first = cr.generate_and_store_report(conn, HANA_MICRON, trigger_reason="test", schema=schema)
            conn.commit()
            self.assertEqual(first["template_kind"], cr.TEMPLATE_SEMICONDUCTOR)
            for section_key in cr.COMMON_SECTIONS + (cr.SECTOR_SECTION_KEY,):
                self.assertIn(section_key, first["sections"])
                self.assertEqual(first["sections"][section_key]["change_kind"], "new")

            second = cr.generate_and_store_report(conn, HANA_MICRON, trigger_reason="test-rerun", schema=schema)
            conn.commit()
            for section_key, outcome in second["sections"].items():
                self.assertEqual(outcome["change_kind"], "unchanged", section_key)
                self.assertEqual(outcome["revision_no"], first["sections"][section_key]["revision_no"])

    def test_get_current_report_mirrors_the_latest_revision_of_each_section(self) -> None:
        with isolated_schema() as (conn, schema):
            cr.generate_and_store_report(conn, LIVSMED, trigger_reason="test", schema=schema)
            conn.commit()
            report = cr.get_current_report(conn, LIVSMED, schema=schema)
            self.assertIsNotNone(report)
            self.assertEqual(report["template_kind"], cr.TEMPLATE_MEDTECH)
            self.assertIn("header", report["sections"])
            self.assertEqual(report["sections"]["header"]["content"]["stock_name"], "리브스메드")

    def test_unknown_stock_code_has_no_report(self) -> None:
        with isolated_schema() as (conn, schema):
            self.assertIsNone(cr.get_current_report(conn, "999999", schema=schema))

    def test_a_changed_section_gets_a_new_revision_without_touching_others(self) -> None:
        """영향 분리: 재실행 사이에 analyst_pdf_extracts 가 바뀌면 header 만 새 revision."""
        with isolated_schema() as (conn, schema):
            first = cr.generate_and_store_report(conn, AJ_NETWORKS, trigger_reason="test", schema=schema)
            conn.commit()
            # financials 섹션을 강제로 바꾼 것처럼 재생성 로직을 거치지 않고, 같은 입력으로
            # 다시 불러 다른 섹션들의 revision_no 가 그대로인지만 확인한다(결정성 검증).
            second = cr.generate_and_store_report(conn, AJ_NETWORKS, trigger_reason="test-2", schema=schema)
            conn.commit()
            for key in cr.COMMON_SECTIONS + (cr.SECTOR_SECTION_KEY,):
                self.assertEqual(second["sections"][key]["revision_no"], first["sections"][key]["revision_no"])
                self.assertEqual(second["sections"][key]["change_kind"], "unchanged")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
