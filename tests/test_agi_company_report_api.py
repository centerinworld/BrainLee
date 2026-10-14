"""AGI-06 기업 보고서 API(routes/agi.py `/company-reports*`) 회귀 테스트.

2026-10-14 외부 검토 반영 — 이전엔 운영 `public` DB에 `stock_code='999999'` 픽스처
행을 테스트가 직접 INSERT/DELETE했다. "자기완결형"이라도 **운영 DB를 테스트가 변경한다**는
사실 자체가 작업 지침("테스트는 일회용 스키마에서 실행")과 맞지 않고, 테스트 중단·동시
실행 시 픽스처가 남을 위험이 있었다. 이제는 `/documents`(`tests/test_agi_documents_api.py`)
와 같은 패턴 — 라우터의 스키마 선택 지점(`agi_route._company_reports_schema`)을
`mock.patch.object`로 바꿔 **일회용 PostgreSQL 스키마**(`isolated_schema()`)를 가리키게
한다. `public`에는 쓰기가 전혀 가지 않는다(검증: 이 모듈이 `public.company_reports`에
INSERT/DELETE 하는 줄이 없다 — 전부 `generate_and_store_report(..., schema=schema)`).

보고서 조립 자체(`generate_and_store_report`)는 기존 운영 테이블(`financial_data`,
`stock_universe` 등, 전부 **읽기만**)에서 실제 데이터를 가져온다 — 이건 `/company-reports`
API 가 애초에 그 숫자들을 조립해 보여주는 것이므로 읽기는 실제 데이터를 쓰는 게 맞다
(`tests/test_agi_company_report.py`의 `StoreAndRetrieveTests`와 같은 전제).

이 엔드포인트는 공개 HTTP 계약에 `schema` 파라미터가 없다 — 운영에서는 항상 `public`을
읽는다(설계상 의도, `_company_reports_schema()` 기본값). 2026-10-14 공개 전환(사용자
지시 "Stock Lab에 공개화면 만들어줘")에 따라 이 두 엔드포인트는 인증 없이 공개된다
(`/documents`와 동일 등급) — 더 이상 `require_owner`를 거치지 않는다.
"""
from __future__ import annotations

import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.agi as agi_route
from agi.company_report import generate_and_store_report
from tests.agi_test_support import isolated_schema

REAL_STOCK_CODE = "067310"  # 하나마이크론 — 실제 financial_data 등으로 조립 가능한 종목


class CompanyReportApiTests(unittest.TestCase):
    def _client(self, schema: str) -> TestClient:
        """라우터를 일회용 스키마로 향하게 한 TestClient(`public`은 손대지 않는다)."""
        app = FastAPI()
        app.include_router(agi_route.router, prefix="/api/agi", tags=["agi"])
        patcher = mock.patch.object(agi_route, "_company_reports_schema", lambda: schema)
        patcher.start()
        self.addCleanup(patcher.stop)
        return TestClient(app)

    def test_list_is_public_and_returns_the_fixture_report(self) -> None:
        with isolated_schema() as (conn, schema):
            generate_and_store_report(conn, REAL_STOCK_CODE, schema=schema)
            conn.commit()
            client = self._client(schema)

            response = client.get("/api/agi/company-reports")
            self.assertEqual(response.status_code, 200)
            body = response.json()
            codes = {item["stock_code"] for item in body["items"]}
            self.assertEqual(codes, {REAL_STOCK_CODE})
            self.assertEqual(body["count"], len(body["items"]))

    def test_get_single_report_is_public_and_mirrors_the_stored_sections(self) -> None:
        with isolated_schema() as (conn, schema):
            generate_and_store_report(conn, REAL_STOCK_CODE, schema=schema)
            conn.commit()
            client = self._client(schema)

            response = client.get(f"/api/agi/company-reports/{REAL_STOCK_CODE}")
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["stock_code"], REAL_STOCK_CODE)
            self.assertEqual(body["template_kind"], "semiconductor")
            self.assertIn("header", body["sections"])
            self.assertEqual(body["sections"]["header"]["content"]["stock_name"], "하나마이크론")
            self.assertIn("financials", body["sections"])

    def test_unknown_stock_code_is_404(self) -> None:
        with isolated_schema() as (conn, schema):
            client = self._client(schema)
            response = client.get("/api/agi/company-reports/999999")
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["detail"]["reason"], "company_report_not_found")

    def test_status_endpoint_lists_company_reports_as_public(self) -> None:
        client = TestClient(self._bare_app())
        response = client.get("/api/agi/status")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("/api/agi/company-reports", body["public_routes"])
        self.assertNotIn("/api/agi/company-reports", body["owner_routes"])

    @staticmethod
    def _bare_app() -> FastAPI:
        app = FastAPI()
        app.include_router(agi_route.router, prefix="/api/agi", tags=["agi"])
        return app


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
