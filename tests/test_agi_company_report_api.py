"""AGI-06 기업 보고서 API(routes/agi.py `/company-reports*`) 회귀 테스트.

이 엔드포인트는 `schema` 파라미터가 없다 — 항상 `public`의 `company_reports`/
`company_report_sections`를 읽는다(설계상 의도 — 운영 API 는 격리 스키마를 모른다).
그래서 임시 스키마로는 격리할 수 없다 — 대신 **이 테스트가 직접 만들고 지우는 전용
픽스처 행**(`stock_code='999999'`, 실제 KRX 종목코드가 아니다)을 `public`에 넣고 뺀다
(2026-10-14 외부 검토 반영: 이전엔 067310 하나마이크론 실데이터 존재를 가정해서 신규/
초기화 환경에서는 테스트가 실패할 수 있었다). 다른 테스트가 쓰는 "999999"가 실제
`stock_universe`/`order_backlog`에 없다는 사실은 `tests/test_agi_company_report.py`가
이미 확인했다.

2026-10-14 공개 전환: 사용자 지시("Stock Lab에 공개화면 만들어줘")에 따라 이 두
엔드포인트는 인증 없이 공개된다(`/documents`와 동일 등급) — 더 이상 `require_owner`를
거치지 않는다.
"""
from __future__ import annotations

import json
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.agi as agi_route
from agi.db import connect_primary

FIXTURE_STOCK_CODE = "999999"
FIXTURE_TEMPLATE_KIND = "generic"


class CompanyReportApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect_primary(timeout=30.0)
        self._clear_fixture()
        now = "2026-10-14T00:00:00"
        self.conn.execute(
            "INSERT INTO company_reports (stock_code, template_kind, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (FIXTURE_STOCK_CODE, FIXTURE_TEMPLATE_KIND, now, now),
        )
        header_content = {"stock_name": "픽스처테스트기업", "sector_large": "테스트", "per": 10.0}
        self.conn.execute(
            "INSERT INTO company_report_sections "
            "(stock_code, template_kind, section_key, revision_no, content_hash, content_json, "
            " change_kind, trigger_reason, generated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                FIXTURE_STOCK_CODE, FIXTURE_TEMPLATE_KIND, "header", 1, "fixturehash1",
                json.dumps(header_content, ensure_ascii=False), "new", "test_fixture", now,
            ),
        )
        self.conn.commit()
        self.addCleanup(self._teardown_fixture)

    def _clear_fixture(self) -> None:
        self.conn.execute(
            "DELETE FROM company_report_sections WHERE stock_code = ?", (FIXTURE_STOCK_CODE,)
        )
        self.conn.execute("DELETE FROM company_reports WHERE stock_code = ?", (FIXTURE_STOCK_CODE,))
        self.conn.commit()

    def _teardown_fixture(self) -> None:
        try:
            self._clear_fixture()
        finally:
            self.conn.close()

    def _client(self) -> TestClient:
        app = FastAPI()
        app.include_router(agi_route.router, prefix="/api/agi", tags=["agi"])
        return TestClient(app)

    def test_list_is_public_and_includes_the_fixture_report(self) -> None:
        client = self._client()
        response = client.get("/api/agi/company-reports")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        codes = {item["stock_code"] for item in body["items"]}
        self.assertIn(FIXTURE_STOCK_CODE, codes)
        self.assertEqual(body["count"], len(body["items"]))

    def test_get_single_report_is_public_and_mirrors_the_stored_sections(self) -> None:
        client = self._client()
        response = client.get(f"/api/agi/company-reports/{FIXTURE_STOCK_CODE}")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["stock_code"], FIXTURE_STOCK_CODE)
        self.assertEqual(body["template_kind"], FIXTURE_TEMPLATE_KIND)
        self.assertIn("header", body["sections"])
        self.assertEqual(body["sections"]["header"]["content"]["stock_name"], "픽스처테스트기업")

    def test_unknown_stock_code_is_404(self) -> None:
        client = self._client()
        response = client.get("/api/agi/company-reports/888888")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["reason"], "company_report_not_found")

    def test_status_endpoint_lists_company_reports_as_public(self) -> None:
        client = self._client()
        response = client.get("/api/agi/status")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("/api/agi/company-reports", body["public_routes"])
        self.assertNotIn("/api/agi/company-reports", body["owner_routes"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
