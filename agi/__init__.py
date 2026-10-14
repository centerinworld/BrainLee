"""AGI 확장(Improvement_Stock_System) 구현 패키지.

이 패키지는 **신설 테이블만** 쓴다. 기존 정본 수집 경로(DART/FnGuide/네이버/가격/텔레그램
수집기)를 덮어쓰지 않는다(계약 `docs/AGI_01_SOURCE_TIME_AUTH_CONTRACT_20261011.md` §3 동결 규칙).

구성
----
    agi/revision.py         원문 revision_hash 정규 알고리즘 (계약 §5-1)
    agi/timeutil.py         시간 4종 변환 단일 지점 (계약 §4, M2 포맷 2종)
    agi/migrations/         신설 스키마 migration up/down + 실행 러너
    agi/repository.py       멱등 upsert 저장소 (소스·run·문서·revision)
    agi/service.py          텔레그램 read-only 시드 수집 + 조회 서비스
    routes/agi.py           /api/agi/* 무부작용 GET

현재 범위(AGI-02 첫 수직 슬라이스): 텔레그램 활성 채널 시드 + 문서/revision 보관 +
재실행 멱등 + 중단재개 + 조회. 첨부 바이트 보관, OCR, 기업 식별, 관심도는 비범위.
"""

from __future__ import annotations

__all__ = ["revision", "timeutil"]
