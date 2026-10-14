"""AGI 신설 스키마 migration 패키지.

실행 러너: `agi.migrations.runner.MigrationRunner`
CLI: `scripts/migrate_agi_research_core.py` (기본 dry-run)

규칙(계약 AGI-01 §11):
  * **rollback(down) 없는 DDL 금지** — up/down 을 같은 승인 단위로 제출한다.
  * 멱등키는 DB 제약(`UNIQUE`)으로 강제한다(애플리케이션 upsert 만으로 대체 금지).
  * 신설 테이블은 키·시간·권한 컬럼을 1급 컬럼으로 갖는다(JSON blob 단독 저장 금지).
  * 적용 실패 시 부분 적용을 남기지 않는다 — up/down 은 한 트랜잭션 단위다.
"""

from __future__ import annotations

from agi.migrations import (
    m0001_research_core,
    m0002_revision_observation,
    m0003_company_reports,
    m0004_skipped_no_store_policy,
)

# 적용 순서 = 목록 순서. 각 항목은 MigrationSpec.
MIGRATIONS = (
    m0001_research_core.MIGRATION,
    m0002_revision_observation.MIGRATION,
    m0003_company_reports.MIGRATION,
    m0004_skipped_no_store_policy.MIGRATION,
)

__all__ = [
    "MIGRATIONS",
    "m0001_research_core",
    "m0002_revision_observation",
    "m0003_company_reports",
    "m0004_skipped_no_store_policy",
]
