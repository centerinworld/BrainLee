"""migration 0003 — 기업 보고서 섹션 저장(`company_report_sections`).

정본 설계: `docs/AGI_06_COMPANY_REPORT_DESIGN_20261013.md`
  (2026-10-13, AGI-06 — "지속 갱신 기업 보고서"의 첫 저장 계층).

설계 결정
---------
* 보고서를 문서 하나로 저장하지 않는다 — **섹션 단위**로 쪼개 저장한다(헤더·실적·사업구성·
  공시·업종고유 등). 트리거가 있었던 섹션만 새 revision 을 만들고, 나머지 섹션은 이전
  revision 을 그대로 참조한다(설계 문서 §3·§4).
* revision 멱등키는 AGI-01 과 같은 패턴이다 — **콘텐츠 해시**(`content_hash`)가 같으면
  새 행을 만들지 않는다(`UNIQUE (stock_code, section_key, content_hash)`). "현재" 섹션은
  별도 포인터 테이블 없이 `(stock_code, section_key)` 별 **가장 큰 `revision_no`** 로
  정의한다(조회 쿼리 1개로 충분하고, 포인터 갱신 레이스를 원천적으로 없앤다).
* `company_reports` 는 소스 레지스트리가 아니라 "이 종목에 보고서가 있다"는 사실 + 적용된
  업종 템플릿만 기록한다(AGI-01 의 `research_sources` 와 역할이 다르다 — 원문 보관 권한
  3분리가 필요 없다. 숫자는 이미 이 시스템의 공개 대시보드 데이터다).
* up/down 은 같은 단위로 제출하고, m0001/m0002 와 동일한 원장(`agi_schema_migrations`)·
  checksum 드리프트 거부 메커니즘을 그대로 쓴다.
"""

from __future__ import annotations

import hashlib

from agi.db import quote_schema

VERSION = "0003_company_reports"
NAME = "company_reports / company_report_sections (AGI-06 섹션 단위 revision 저장)"
DESCRIPTION = (
    "AGI-06 기업 보고서의 첫 저장 계층. 섹션별 콘텐츠 해시 기반 revision — 신규 2테이블. "
    "데이터 문장 없음(DDL만)."
)

TABLES = ("company_reports", "company_report_sections")
DOWN_TABLES = ("company_report_sections", "company_reports")

_REPORTS_DDL = """
CREATE TABLE IF NOT EXISTS {p}.company_reports (
    stock_code     TEXT PRIMARY KEY,
    template_kind  TEXT NOT NULL
                   CHECK (template_kind IN ('semiconductor','financial_holding','medtech',
                                            'order_backlog_industry','dividend_utility',
                                            'holding_sotp','materials_value_chain','generic')),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_SECTIONS_DDL = """
CREATE TABLE IF NOT EXISTS {p}.company_report_sections (
    id             BIGSERIAL PRIMARY KEY,
    stock_code     TEXT NOT NULL REFERENCES {p}.company_reports(stock_code),
    template_kind  TEXT NOT NULL,
    section_key    TEXT NOT NULL,
    revision_no    INTEGER NOT NULL,
    content_hash   TEXT NOT NULL,
    content_json   TEXT NOT NULL,
    change_kind    TEXT NOT NULL DEFAULT 'new' CHECK (change_kind IN ('new','updated')),
    trigger_reason TEXT NOT NULL,
    generated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ux_crs_stock_section_hash UNIQUE (stock_code, section_key, content_hash),
    CONSTRAINT ux_crs_stock_section_no   UNIQUE (stock_code, section_key, revision_no)
)
"""

_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS ix_crs_latest ON {p}.company_report_sections "
    "(stock_code, section_key, revision_no DESC)",
)

_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS {p}.agi_schema_migrations (
    version    TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    checksum   TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied_by TEXT
)
"""


def up_statements(schema: str | None = None) -> list[str]:
    prefix = quote_schema(schema)
    statements = [_LEDGER_DDL, _REPORTS_DDL, _SECTIONS_DDL, *_INDEX_DDL]
    return [statement.format(p=prefix).strip() for statement in statements]


def down_statements(schema: str | None = None) -> list[str]:
    prefix = quote_schema(schema)
    statements = [f"DROP TABLE IF EXISTS {prefix}.{table}" for table in DOWN_TABLES]
    statements.append(f"DELETE FROM {prefix}.agi_schema_migrations WHERE version = '{VERSION}'")
    return statements


def checksum(schema: str | None = None) -> str:
    payload = "\n--\n".join(up_statements(None))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class MigrationSpec:
    __slots__ = ("version", "name", "description", "tables")

    def __init__(self, version: str, name: str, description: str, tables: tuple[str, ...]) -> None:
        self.version = version
        self.name = name
        self.description = description
        self.tables = tables

    def up(self, schema: str | None = None) -> list[str]:
        return up_statements(schema)

    def down(self, schema: str | None = None) -> list[str]:
        return down_statements(schema)

    def checksum(self, schema: str | None = None) -> str:
        return checksum(schema)

    def __repr__(self) -> str:  # pragma: no cover - 진단용
        return f"<MigrationSpec {self.version} tables={len(self.tables)}>"


MIGRATION = MigrationSpec(VERSION, NAME, DESCRIPTION, TABLES)
