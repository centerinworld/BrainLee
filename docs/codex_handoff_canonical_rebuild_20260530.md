# Codex 작업 핸드오프 (2026-05-30)

## 1) 에이엘티(172670) 재점검 결과
- 확인 대상: `26년1Q` CFS/OFS 재무제표 + 현금흐름표
- 확인 방식: API 실조회
  - `GET /api/dashboard/financial-table/172670?type=quarter&report_type=CFS`
  - `GET /api/dashboard/financial-table/172670?type=quarter&report_type=OFS`
  - `GET /api/dashboard/cashflow/172670?type=quarter&report_type=CFS`
  - `GET /api/dashboard/cashflow/172670?type=quarter&report_type=OFS`

### 결과
- CFS 26년1Q: 자산 1950, 부채 1037, 자본 912 (억원)
- OFS 26년1Q: 자산 1929, 부채 1026, 자본 904 (억원)
- CFS 26년1Q CF: OCF 123, ICF -40, FCF -87, CAPEX 25
- OFS 26년1Q CF: OCF 120, ICF -40, FCF -87, CAPEX 25

## 2) 전종목 분기 B/S 등식 불일치 일괄 스캔/보정
- 신규 스크립트: `scripts/ops/fix_quarterly_bs_identity_all.py`
- 기준: `is_annual=0`, `quarter in (1..4)`
- 룰:
  - 자산/부채/자본 존재 + `| (자산-부채)-자본 | > max(자산*1%, 5억원)` => 자본 보정
  - 자본 NULL + 자산/부채 존재 => 자본 = 자산-부채
- 로그 적재: `financial_fix_log`

### 실행 결과
- 보정 총 594건
  - CFS 243건
  - OFS 351건
- 잔여 불일치: 0건

## 3) canonical 스키마 + write_gate 도입

### 추가 파일
- `data_write_gate.py`
  - `ensure_canonical_schema()`
  - `gate_financial_row()`
  - `gate_cashflow_row()`
  - `upsert_canonical_financial()`
  - `upsert_canonical_cashflow()`

### 신규 테이블
- `canonical_financial_data`
- `canonical_cashflow_data`
- `write_gate_log`

### 적용 포인트
- `crud.py`의 `upsert_financial_data()`
  - 저장 전 gate 적용
  - 저장 후 canonical 동기화
- `main.py`의 `_upsert_cashflow()`
  - 저장 전 gate 적용 (Q1 q필드 보정 포함)
  - 저장 후 canonical 동기화

## 4) 2022~2025 canonical 재빌드
- 신규 스크립트: `scripts/ops/rebuild_canonical_2022_2025.py`
- 대상 기간: 2022~2025
- 룰:
  - 키별(종목/연도/분기/연결별) 후보 중 quality score 최고 행 선택
  - write_gate 통과/보정 후 canonical upsert

### 실행 결과
- canonical_financial_data: 90,297행
- canonical_cashflow_data: 78,322행

## 5) 종목별 신뢰도 리포트 자동 생성
- 신규 스크립트: `scripts/ops/build_stock_reliability_report.py`
- 출력 파일:
  - `scratch/stock_reliability_report_20260530_234130.csv`
- 컬럼:
  - 분기 P&L 커버리지, B/S 등식 정상 건수, CF q필드 커버리지, validation 반영률, 최종 reliability_score

## 6) 운영상 메모
- 백엔드 재시작 완료 후 write_gate 반영 상태
- `write_gate_log` 누적 기록으로 사후 감사 가능

## 7) Claude 재검증 요청 항목
1. `stock_reliability_report_20260530_234130.csv` 상위/하위 30종목 spot-check
2. `financial_fix_log`에서 `run_id='codex_bs_identity_all_20260530'` 샘플 검증
3. canonical vs 원본(`financial_data`, `cash_flow_data`) diff 샘플 검증
4. 2026년 증분 데이터에 대해 동일 gate가 누락 없이 적용되는지 모니터링
