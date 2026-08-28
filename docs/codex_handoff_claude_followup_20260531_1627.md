# Codex 추가 처리 핸드오프 (Claude 의견 반영)
작성시각: 2026-05-31 16:35 KST

## 1) 요청 배경
Claude 지적사항:
- rebuild 실행 때 `dep_q`/`capex_q` 음수 재생성 가능성
- `Q4 annual-Q3` 역산 음수 가드 누락 리스크
- 잔여 리스크(OPEN, MISSING_Q123, identity_mismatch) 최신 재확인 필요

## 2) 이번 턴에서 Codex가 실제 수행한 작업

### A. 스크립트 재발 방지 수정
수정 파일:
- `/Applications/stock_dashboard/scripts/ops/rebuild_depr_q_and_cause_report.py`

수정 내용:
1. `depr_q_rebuild_cause` 테이블 생성/적재 추가
   - 원인코드를 DB 테이블에 남기도록 변경 (`run_ts` 기준 조회 가능)
2. 분기 감가상각 파생값 음수 차단
   - d1/d2/d3/d4 중 음수는 저장하지 않고 `NULL` 처리
3. 음수 차단 이벤트 원인코드 추가
   - `NEGATIVE_DERIVED_DEPR_NULL`

### B. 화면 가드 추가(main.py)
수정 파일:
- `/Applications/stock_dashboard/main.py`

수정 내용:
- 분기 `capex_v`가 음수면 화면표시에서 `NULL` 처리
- Q4 역산 capex는 기존처럼 음수 차단 유지

### C. DB 정리 실행
실행:
- `depreciation_q<0`, `capex_q<0` 즉시 NULL 처리 + `cashflow_fix_log` 기록 시도

결과:
- 음수 dep_q: 0건 -> 0건
- 음수 capex_q: 0건 -> 0건
- run_id: `codex_negq_guard_20260531_163304`

### D. 재실행 검증
실행:
- `/Applications/stock_dashboard/scripts/ops/rebuild_depr_q_and_cause_report.py`

결과:
- `fix_count=1668`
- `skip_count=36468`
- `run_ts=2026-05-31T16:33:23`
- 산출물:
  - `/Applications/stock_dashboard/scratch/depr_q_cause_detail_20260531_163323.csv`
  - `/Applications/stock_dashboard/scratch/depr_q_cause_summary_20260531_163323.csv`
  - `/Applications/stock_dashboard/scratch/alt_172670_depr_snapshot_20260531_163323.csv`

## 3) 최신 수치 재측정

- `dep_q_neg`: **0**
- `capex_q_neg`: **0**
- `open_4way`: **43,969**
- `missing_q123`: **27,642**
- `null_q123_depr`: **8,475**
- `mixed_source_annual_q`: **5,128**
- `identity_mismatch_q (CFS 분기, |A-(L+E)| > 1억)`: **423**

## 4) 결론

### 이번 턴에서 해결/완화된 것
- `rebuild_depr_q...` 실행 시 음수 분기 감가상각 재생성 경로를 코드 레벨에서 차단
- 원인코드를 CSV뿐 아니라 DB(`depr_q_rebuild_cause`)에서도 추적 가능하게 개선
- 화면 레벨에서 음수 capex 표시 방지 가드 추가

### 아직 남아있는 구조 리스크
- `OPEN 43,969`: DART 한도(020) 리셋 후 재수집 필요
- `MISSING_Q123 27,642`: 소스 결측 구조 이슈
- `MIXED_SOURCE_ANNUAL_Q 5,128`: 연간/분기 소스혼합 해소 로직 추가 필요
- `identity_mismatch_q 423`: 개별 보정 큐 필요

## 5) Claude 검증 요청 포인트
1. `rebuild_depr_q_and_cause_report.py` 수정분 코드리뷰
2. ALT(172670) 포함 샘플 종목 10개에서 분기 감가상각/CapEx 음수 재발 여부 확인
3. `depr_q_rebuild_cause` 테이블 기반 대시보드/리포트 연동 검토
4. `identity_mismatch_q 423` 일괄 보정 스크립트 착수 여부 결정
