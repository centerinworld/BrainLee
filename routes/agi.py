"""routes/agi.py — AGI 원문 보관 조회 API (2026-10-11 신규, AGI-02 첫 수직 슬라이스)

공개(인증 없음)
    GET /api/agi/documents                  원문 문서 목록 — 무부작용
    GET /api/agi/documents/{document_id}    원문 1건 + revision 이력 — 무부작용
    GET /api/agi/status                     신설 스키마 적용 상태 — 무부작용
    GET /api/agi/company-reports            AGI-06 기업 보고서 목록(2026-10-14 신규, 공개)
    GET /api/agi/company-reports/{code}     AGI-06 기업 보고서 1건 — 섹션별 최신 revision(공개)

관리자 전용(인증 필요)
    GET /api/agi/admin/documents            비공개·미승인 원문 포함 목록
    GET /api/agi/admin/documents/{id}       비공개·미승인 원문 1건 + revision 이력

권한 경계 (계약 `docs/AGI_01_SOURCE_TIME_AUTH_CONTRACT_20261011.md`)
------------------------------------------------------------------
* §9-1: prefix 는 `/api/agi/*` — `routes/research_lab.py` 가 점유한 `/api/research/*` 를
  재사용하지 않는다.
* §9-2/§9-3: 조회는 **무부작용**(SELECT 만)이고, 응답 봉투에 `as_of`,
  `snapshot_generation_time`, `source_watermarks`, `quality_summary`, `unconfirmed_fields`
  를 포함한다.
* §9-4: 공개 화면에는 `publish_summary != 'denied'` 인 근거만 노출하고, **개인 대화·비공개
  원문·미발행 글은 인증된 연구 화면**에 둔다.

공개 노출은 **allowlist** 다(`agi.repository.PUBLIC_SCOPES` = `public_channel`/`blog`/`report`).
`scope` 가 그 밖인 소스(`private_chat`, `other`=공개 미증명 미분류)는 `is_private=false` +
`publish_summary='allowed'` 조합이어도 **목록·상세·watermark 어디에도** 나오지 않는다 —
denylist(`<> 'private_chat'`)가 아니라 allowlist 인 이유는, 스키마에 새 scope 가 추가될 때
그 값이 조용히 공개로 승격되지 않게 하기 위해서다(fail-closed). 회귀 테스트가
스키마 CHECK 값 − allowlist == `NON_PUBLIC_SCOPES` 를 강제한다.

그래서 공개 경로는 `include_private` 같은 **외부 쿼리 파라미터를 받지 않는다**. 노출 등급은
서버가 경로로만 결정하고 필터는 **SQL 단계**(`agi.repository.*_visibility_clauses`)에서 건다 →
인증 여부·터널 여부와 무관하게 비공개 원문이 공개 응답에 섞이지 않는다.

관리자 경로는 두 겹으로 보호된다.
  ① 경로 자체가 기존 게이트의 owner 등급이다 — `security_gate.OWNER_GET_PATTERN`
     (`/(…|admin|…)(/|$)`) 이 `/api/agi/admin/...` GET 을 `owner` 로 분류하고 미들웨어
     `api_token_gate` 가 강제한다(회귀 테스트로 고정).
  ② 핸들러가 `security_gate._owner_ok(request)` 를 **다시** 확인한다(터널 감지에만 의존하지
     않도록). 인증 실패는 401 `owner_required`.

⚠️ 이 라우터는 기존 정본 수집 경로(DART/FnGuide/네이버/가격/텔레그램 수집기)를 호출하지 않고,
주문·포트폴리오·외부 전송 경로를 import 하지 않는다. DB 쓰기·외부 호출이 없다(SELECT 전용).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from agi.repository import (
    INTERNAL_VISIBILITY,
    PUBLIC_SCOPES,
    PUBLIC_VISIBILITY,
    document_visibility_clauses,
)
from agi.service import ResearchIngestionService

router = APIRouter()


def connect_readonly():
    """읽기 전용 primary 연결. 테스트가 임시 스키마 연결로 대체한다."""
    from agi.db import connect_primary

    return connect_primary(readonly=True, timeout=30.0)


def get_service() -> ResearchIngestionService:
    return ResearchIngestionService()


async def require_owner(request: Request) -> None:
    """기존 게이트의 owner 판정 재사용 — 실패하면 401(fail-closed)."""
    import security_gate as sg

    if not await sg._owner_ok(request):
        raise HTTPException(
            status_code=401,
            detail={
                "reason": "owner_required",
                "message": "비공개·미승인 원문은 관리자 인증이 필요하다(security_gate owner 단계).",
            },
        )


@router.get("/documents")
def list_documents(
    source_id: str | None = Query(default=None, description="research_sources.source_id"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """공개 목록 — 개인 대화와 `publish_summary='denied'` 근거는 SQL 에서 제외된다."""
    conn = connect_readonly()
    try:
        return get_service().documents_envelope(
            conn, source_id=source_id, limit=limit, offset=offset, visibility=PUBLIC_VISIBILITY
        )
    finally:
        conn.close()


@router.get("/documents/{document_id}")
def get_document(document_id: str):
    conn = connect_readonly()
    try:
        envelope = get_service().document_envelope(
            conn, document_id, visibility=PUBLIC_VISIBILITY
        )
    finally:
        conn.close()
    if envelope is None:
        raise HTTPException(
            status_code=404,
            detail={"reason": "document_not_found", "document_id": document_id},
        )
    return envelope


@router.get("/admin/documents")
async def admin_list_documents(
    request: Request,
    source_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """관리자 전용 — 비공개·미승인 원문 포함. 경로가 게이트 owner 등급이고 핸들러가 재확인한다."""
    await require_owner(request)
    conn = connect_readonly()
    try:
        return get_service().documents_envelope(
            conn, source_id=source_id, limit=limit, offset=offset, visibility=INTERNAL_VISIBILITY
        )
    finally:
        conn.close()


@router.get("/admin/documents/{document_id}")
async def admin_get_document(request: Request, document_id: str):
    await require_owner(request)
    conn = connect_readonly()
    try:
        envelope = get_service().document_envelope(
            conn, document_id, visibility=INTERNAL_VISIBILITY
        )
    finally:
        conn.close()
    if envelope is None:
        raise HTTPException(
            status_code=404,
            detail={"reason": "document_not_found", "document_id": document_id},
        )
    return envelope


@router.get("/status")
def agi_status():
    """신설 스키마 적용 상태. 마이그레이션은 여기서 실행하지 않는다(읽기 전용)."""
    from agi.migrations import MIGRATIONS

    conn = connect_readonly()
    try:
        tables_present = (
            conn.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = %s",
                ("research_documents",),
            ).fetchone()
            is not None
        )
        applied = []
        for migration in MIGRATIONS:
            ledger = None
            if tables_present:
                ledger = conn.execute(
                    "SELECT version, applied_at FROM agi_schema_migrations WHERE version = %s",
                    (migration.version,),
                ).fetchone()
            applied.append(
                {
                    "version": migration.version,
                    "name": migration.name,
                    "applied": ledger is not None,
                    "applied_at": str(ledger["applied_at"]) if ledger is not None else None,
                    "tables": list(migration.tables),
                    "checksum": migration.checksum(None),
                }
            )
    finally:
        conn.close()
    return {
        "namespace": "/api/agi",
        "public_routes": [
            "/api/agi/documents",
            "/api/agi/status",
            "/api/agi/company-reports",
        ],
        "owner_routes": ["/api/agi/admin/documents"],
        "public_source_scopes": list(PUBLIC_SCOPES),
        "public_scope_note": (
            "공개 경로가 노출하는 소스 scope allowlist. 이 밖의 scope(private_chat/other 및 "
            "스키마에 새로 추가되는 값)는 어떤 상태값 조합에서도 공개되지 않는다(fail-closed)."
        ),
        # 감사 가능성(AGI-03): 공개 조회의 **전체 조건**을 응답에 싣는다. 로드된 서버가 어떤
        # 필터를 쓰는지 HTTP 응답만으로 확인할 수 있어야 한다 — `public_source_scopes` 만으로는
        # `is_private`·`publish_summary`·`access_state` 조건이 서빙 중인지 판별할 수 없다.
        "public_document_conditions": document_visibility_clauses(PUBLIC_VISIBILITY),
        "internal_document_conditions": document_visibility_clauses(INTERNAL_VISIBILITY),
        "migrations": applied,
        "live_apply_performed_by_this_process": False,
        "note": "적용은 scripts/migrate_agi_research_core.py --apply (사람 승인 단위)로만 한다.",
    }


# --------------------------------------------------------------------- AGI-06 기업 보고서
# (2026-10-14 신규, 2026-10-14 공개 전환 — 사용자 지시 "Stock Lab에 공개화면 만들어줘")
# 구성 요소는 agi/company_report.py 참고. 숫자 데이터는 기존 운영 테이블(financial_data
# 등, 이미 대시보드 다른 화면에서 공개 노출됨)을 그대로 조립한 것이라 개인정보·비공개
# 원문 성격이 아니다 — `/documents`와 같이 인증 없이 공개한다. 이 핸들러는 SELECT 전용이다
# — 보고서 생성(쓰기)은 `agi.company_report.generate_and_store_report()`를 launchd
# 스케줄(`agi_company_report_refresh_20261014.py`)로만 부르고, 이 라우터에는 쓰기가 없다.
@router.get("/company-reports")
def list_company_reports():
    conn = connect_readonly()
    try:
        rows = conn.execute(
            "SELECT stock_code, template_kind, created_at, updated_at FROM company_reports "
            "ORDER BY updated_at DESC"
        ).fetchall()
        items = [dict(row) for row in rows]
    finally:
        conn.close()
    return {"items": items, "count": len(items)}


@router.get("/company-reports/{stock_code}")
def get_company_report(stock_code: str):
    from agi.company_report import get_current_report

    conn = connect_readonly()
    try:
        report = get_current_report(conn, stock_code)
    finally:
        conn.close()
    if report is None:
        raise HTTPException(
            status_code=404,
            detail={"reason": "company_report_not_found", "stock_code": stock_code},
        )
    return report
