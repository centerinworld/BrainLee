# Root Cause 분석/실행 결과 (2026-05-31)

요청사항:
1. `depreciation_q` 재계산/보정 배치 실행
2. 분기 매출/이익/현금흐름 1:1 대조 기반 원인코드 리포트 생성
3. 결과를 클로드 재검증용 문서로 정리

---

## A. 이번 턴에서 실제 실행한 것

### A-1) 전종목 `depreciation_q` 재계산 배치 실행
- 실행 스크립트(신규):
  - `/Applications/stock_dashboard/scripts/ops/rebuild_depr_q_and_cause_report.py`
- 수행 내용:
  - `cash_flow_data.depreciation_q` 컬럼 자동 생성(없으면)
  - 연도/종목/report_type 단위로 Q1~Q3 누적 + 연간값 기반 분기 D&A 재계산
  - 결과를 `depreciation_q_fix_log`에 기록
  - 원인코드(`MIXED_SOURCE_ANNUAL_Q` 등) 상세/요약 리포트 출력

- 실행 결과:
  - `fix_count=34257`
  - `skip_count=35817`

### A-2) 원인코드 리포트 생성
- 상세:
  - `/Applications/stock_dashboard/scratch/depr_q_cause_detail_20260531_001019.csv`
- 요약:
  - `/Applications/stock_dashboard/scratch/depr_q_cause_summary_20260531_001019.csv`
- ALT 스냅샷:
  - `/Applications/stock_dashboard/scratch/alt_172670_depr_snapshot_20260531_001019.csv`

### A-3) 상세분석 엑셀 1:1 대조(샘플 자동) 리포트
- 출력:
  - `/Applications/stock_dashboard/scratch/alt_and_peers_1to1_compare_20260531.csv`
- 목적:
  - 상세분석 페이지에 연결된 엑셀 파일 기준 분기값 vs DB 분기값 불일치 탐지

---

## B. 원인코드 요약 (전종목)

`depr_q_cause_summary_20260531_001019.csv` 기준:

- `MISSING_Q123`: 27,642
- `NULL_Q123_DEPR`: 7,794
- `MIXED_SOURCE_ANNUAL_Q`: 5,594
- `Q4_SPIKE_FROM_ANNUAL_DELTA`: 2,261
- `NON_MONOTONIC_CUMULATIVE`: 860
- `MISSING_ANNUAL_DEPR`: 381

핵심 해석:
1. 가장 큰 축은 **Q1~Q3 분기소스 부재/누락**
2. 두 번째는 **연간 vs 분기 소스혼합(MIXED_SOURCE)**
3. 세 번째는 그 결과로 나타나는 **Q4 급등치(SPIKE)**

---

## C. ALT(172670) 집중 분석 결론

ALT cause rows:
- 2023 CFS: `MIXED_SOURCE_ANNUAL_Q`
- 2024 CFS: `MIXED_SOURCE_ANNUAL_Q`
- 2025 CFS: `Q4_SPIKE_FROM_ANNUAL_DELTA`
  - note: `d1=82.2억, d2=59.7억, d3=53.8억, d4=149.8억, annual=345.5억, q3cum=195.7억`

즉 ALT의 25Q4 D&A 급증은:
- 단순 오타가 아니라,
- `연간( fnguide_seibro )`와 `분기( fnguide cumulative )` 체인이 결합되어
- `Q4 = annual - Q3cum` 역산 시 과대치가 만들어지는 구조 문제.

---

## D. 1:1 대조에서 확인된 공통 패턴

`alt_and_peers_1to1_compare_20260531.csv`에서 high mismatch 종목(아이센스/한화오션/포인트모바일 등) 공통:

1. **단위 혼합(억원/백만원) 리스크**
2. **CAPEX 부호 규약 차이** (지출 음수 vs 양수)
3. **누적값/분기값 혼합**
4. **연간 소스와 분기 소스 불일치**

---

## E. 근본 개선안 (코드/데이터 규칙)

### E-1) 분기 D&A를 first-class 필드화
- `depreciation_q`를 공식 분기표시 필드로 사용
- 화면/전략/검증 모두 `depreciation_q`만 사용
- `depreciation`은 누적/원천 보존용으로만 사용

### E-2) Q4 생성 게이트 강화
- `annual_source_family == q3_source_family`일 때만 Q4 생성
- 다르면 `UNVERIFIED_MIXED_SOURCE`로 보류
- 보류값은 화면에서 추정값으로 표시 금지

### E-3) 분기 확정/추정 레벨 분리
- 확정: 원천 분기값 (또는 동일 체인 역산 + 검증 통과)
- 추정: 체인 혼합/보정 추정
- 투자 로직은 확정값 우선

### E-4) 1:1 대조 파이프라인 정식화
- 상세분석 연결 xlsx를 nightly로 파싱
- 필드별 오차율, 부호오차, 단위오차를 cause-code로 적재
- 누적 오차 높은 종목은 자동 `manual_review_queue` 등록

---

## F. 클로드 재검증 요청 체크리스트

1. `depreciation_q` 기반으로 ALT/상위 mismatch 종목(10개) 화면표시값 재검증
2. `MIXED_SOURCE_ANNUAL_Q` 종목에서 Q4 생성차단 규칙 적용 시 영향 분석
3. CAPEX 부호 표준화(저장/표시 분리) 설계 재검토
4. 1:1 비교 스크립트의 단위추론 정확도 개선(백만원/억원/천원)

