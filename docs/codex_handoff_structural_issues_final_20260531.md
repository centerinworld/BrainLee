# Codex → Claude 최종 보고서 (대량 DB 수정 실행 결과)
작성일: 2026-05-31 11:55 KST

## 1. 이번 턴 실제 실행 내역
- 대량 보정 1차 실행
  - 명령: `./venv/bin/python scripts/ops/repair_financial_cashflow_with_logs.py`
  - 결과: **9,702 필드 수정**
  - 산출물: `/Applications/stock_dashboard/scratch/financial_cashflow_repair_20260531_114241.json`
- 캐노니컬 재빌드
  - 명령: `./venv/bin/python scripts/ops/rebuild_canonical_2022_2025.py`
  - 결과: `canonical_financial_rows=90,297`, `canonical_cashflow_rows=78,322`
- IFRS 통합 재검증 스크립트 수정/실행
  - 파일: `/Applications/stock_dashboard/scripts/ops/ifrs_unified_mapping_revalidate.py`
  - 조치: f-string 백슬래시 문법 오류 수정
  - 결과: 파이프라인 완주, QA PASS 출력
- 감가상각 분기 재구축
  - 명령: `./venv/bin/python scripts/ops/rebuild_depr_q_and_cause_report.py`
  - 결과: **1,979건 보정**
  - 산출물:
    - `/Applications/stock_dashboard/scratch/depr_q_cause_summary_20260531_115257.csv`
    - `/Applications/stock_dashboard/scratch/depr_q_cause_detail_20260531_115257.csv`

## 2. 최신 품질 수치(실측)
- QUARTERLY_4WAY: total 419,825 / OK 285,823(68.1%) / OPEN 43,969 / STRUCTURAL 90,033 / AMBIGUOUS 0
- ANNUAL_CONSISTENCY: total 32,172 / OK 23,708(73.7%) / OPEN 0 / STRUCTURAL 8,464 / AMBIGUOUS 0
- DART_NAVER_CROSS: total 28,156 / OK 22,588(80.2%) / AMBIGUOUS 0
- DART_FG_CROSS: total 3,422 / OK 1,373(40.1%) / AMBIGUOUS 0
- OFS_ANNUAL_CONSISTENCY: total 34,713 / OK 16,615(47.9%) / AMBIGUOUS 0

### CF 핵심 잔여(2022+, CFS 분기)
- total 37,740
- depreciation_q NULL 10,757
- capex_q NULL 7,168
- both NULL 4,229
- mixed_source_annual_q 7,096

## 3. 잔여 리스크 원인코드
`depr_q_cause_summary_20260531_115257.csv` 기준
- MISSING_Q123: 27,642
- NULL_Q123_DEPR: 8,475
- MIXED_SOURCE_ANNUAL_Q: 5,128
- Q4_SPIKE_FROM_ANNUAL_DELTA: 1,866
- MISSING_ANNUAL_DEPR: 351
- NON_MONOTONIC_CUMULATIVE: 276

## 4. 차단사항/주의사항
- `resolve_quarterly_risks_with_dart.py --year-from 2022 --year-to 2026` 실행 시 DART 응답 `status=020`(한도초과) 반복.
- 장시간 반복 프로세스는 중지함.
- 따라서 DART 재수집 의존 개선(OPEN 직접 축소)은 한도 리셋 후 재시도 필요.

## 5. Claude 재검증 체크리스트(우선순위)
1. `financial_cashflow_repair_20260531_114241.json` 수정행 샘플 50건 정합성 확인
2. `MIXED_SOURCE_ANNUAL_Q` 7,096건 중 시총 상위 샘플 점검 (연간/Q4 source harmonization 필요성 판정)
3. ALT(172670), 휴온스(243070), 감성코퍼레이션(036620) 화면 값 재검증
4. DART 한도 리셋 후 `resolve_quarterly_risks_with_dart.py` 재실행 및 OPEN 감소량 재측정

## 6. 관련 문서
- 기존 지시서(최신 실행결과 반영 완료):
  - `/Applications/stock_dashboard/scratch/codex_handoff_structural_issues_20260531.md`
- 실행/정책 문서:
  - `/Applications/stock_dashboard/docs/codex_handoff_fnguide_reliability_execution_20260531.md`
