# Codex Handoff — 4중 검증 원인분석/수정큐 (2026-05-25)

## 0) 요청 배경
사용자 정책 확정:
- DART는 공식 원천(anchor)이며, 외부 2소스(FnGuide/Naver) 일치만으로 자동 확정 금지.
- DART와 외부가 충돌하면 반드시 원인 분석 후 판단.
- 4중 검증( DART/FnGuide/Naver/Seibro ) 기준으로 재검증 및 수정큐 운영.

## 1) 이번 턴에서 실제 수행한 작업

### 1-1. 정책 문서 반영
- 파일: `/Applications/stock_dashboard/CLAUDE.md`
- 추가 섹션: **DART 불일치 처리 원칙 (사용자 확정 지시, 2026-05-25)**
- 핵심 추가:
  - `dart_mismatch_all` 분류 시 원인분석 의무
  - 자동치환 금지
  - 로그/리포트 필수

### 1-2. 4중 재검증/수정큐 스크립트 신규
- 파일: `/Applications/stock_dashboard/scratch/financial_4way_revalidate_and_queue.py`
- 기능:
  1. `seibro_financial_snapshot` 테이블 생성/업서트
  2. 4소스(DART/FnGuide/Naver/Seibro) 재검증
  3. `dart_mismatch_all` 자동 탐지
  4. `financial_fix_log`에 수정큐 적재 (`fix_rule='QUEUE_DART_MISMATCH_ALL'`)

### 1-3. 재검증 실행
- 실행 커맨드:
  - `python3 scratch/financial_4way_revalidate_and_queue.py --revalidate --run-id 4way_full_20260525_01`
- 결과:
  - `financial_fix_log` 적재: **4,189건**

## 2) 산출물(증빙 파일)

- 상세: `/Applications/stock_dashboard/docs/verification/financial_4way_detail_20260525_132331.csv`
- 요약: `/Applications/stock_dashboard/docs/verification/financial_4way_summary_20260525_132331.csv`
- DART 불일치 큐: `/Applications/stock_dashboard/docs/verification/financial_4way_dart_mismatch_queue_20260525_132331.csv`
- 연간 매출 원인 버킷: `/Applications/stock_dashboard/docs/verification/financial_4way_rootcause_annual_revenue_20260525_132331.csv`
- 4중 상태 보고: `/Applications/stock_dashboard/docs/verification/financial_4way_validation_status_20260525.md`

## 3) 핵심 결과 요약

### 3-1. `financial_fix_log` 큐 적재
- run_id: `4way_full_20260525_01`
- fix_rule: `QUEUE_DART_MISMATCH_ALL`
- 건수: **4,189건**

### 3-2. 연간 revenue 충돌(대표)
- `dart_mismatch_all`: **3,768건**
- 주요 버킷:
  - `dart_vs_fg_naver_consensus_conflict|dart_lower_15pct_plus`: 2,174건
  - `dart_vs_fg_naver_consensus_conflict`: 1,494건
  - `dart_vs_fg_naver_consensus_conflict|dart_higher_15pct_plus`: 100건

### 3-3. 네오티스(085910) 확인
- 2025년 매출: DART `49,839,363,309` vs FnGuide/Naver `68,700,000,000`
- 분류: `dart_vs_fg_naver_consensus_conflict|dart_lower_15pct_plus`
- 2023/2024/2025 모두 동일 축 충돌 존재.

## 4) 왜 이런 일이 발생했는가 (원인 분석)

1. **검증과 표시정책의 분리 미완성**
- 기존 4중 검증은 파싱오류/NULL/부호/구조적 예외를 잘 잡았지만,
- `충돌 시 무엇을 화면에 표시할지` 정책(결정엔진)이 항목별로 완결되지 않았음.

2. **연간 CFS 내부의 다중 원천 공존**
- 같은 종목/연도에도 DART 연간행과 FnGuide 연간행이 공존.
- 서로 값이 다를 때 우선순위가 화면/엔드포인트마다 일관되지 않으면 사용자 체감 오류가 발생.

3. **Seibro 4번째 축의 커버리지 부족**
- 현재 `fin_quarterly_validation_flags` 기준 `seibro_value`는 89건(0.43%)만 존재.
- 전종목 완전 4중 검증으로 보기 어려운 상태.

4. **Seibro P&L API 구조 제약**
- 다수 P&L 액션(`incomeStatementTableList`, `profitLossTableList` 등) 호출 시 `서버오류2` 반환.
- 현재 공개 호출로 안정적으로 확보 가능한 것은 CF 기반 일부 축(NI 포함 일부) 중심.

## 5) Seibro 확장 배치 진행 결과

### 5-1. 시도 내용
- `--seibro-expand` 로직 구현 완료.
- 다양한 Seibro P&L action 후보를 탐색해 revenue/op/net_income 매핑 시도.

### 5-2. 실제 결과
- 테스트 실행(`--max-stocks 5`) 결과: `ok=0, fail=5`
- 원인: Seibro 서버가 P&L 후보 액션에 대해 `서버오류2` 반환.
- 결론: **코드 구현 완료, 그러나 Seibro 서버 액션/권한/엔드포인트 제약으로 데이터 확장 실패**.

## 6) 앞으로 어떻게 해야 하는가 (실행 우선순위)

### P0 (즉시)
1. `financial_fix_log` 큐(4,189건) 기반 triage 배치 실행
   - 우선순위: `annual + revenue + dart_lower_15pct_plus`
2. 종목별 원인코드 확정
   - `공시 구조 차이`, `Q4/연간 인식 차이`, `DART 매핑 의심`, `외부 파싱 의심`
3. 화면 노출 정책 고정
   - 값 + `source_badge` + `confidence_badge` 동시 노출

### P1 (단기)
1. Seibro 실동작 액션명/업무 태스크 확인
   - 현재 후보 액션은 서버오류2. 유효 action/task 확보 필요.
2. 확보 후 `seibro_financial_snapshot` 재수집
   - annual/quarter revenue/op/net_income 채우기
3. 4중 재검증 재실행
   - 동일 스크립트 `--revalidate`

### P2 (중기)
1. `dart_mismatch_all` 자동 원인분류 모델 고도화
2. 근거 로그(원천 raw, ratio, 선택근거) 누적 저장 표준화
3. 주기 배치
   - 매일: 수집→검증→큐 적재→리포트

## 7) 클로드에게 바로 요청할 실행 목록

1. `financial_fix_log` run_id=`4way_full_20260525_01` 대상 우선 triage SQL/스크립트 작성
2. `annual revenue` 3,768건을 버킷별 샘플링 점검 후 규칙 확정
3. Seibro P&L 유효 액션명 탐색(브라우저 네트워크/원본 앱 분석 포함)
4. 유효 액션 확보 시 `financial_4way_revalidate_and_queue.py --seibro-expand --max-stocks 0` 재실행
5. 재실행 후 4중 리포트 갱신 및 사용자 화면 배지 정책 반영

## 8) 참고 명령

```bash
# 4중 재검증 + 수정큐 적재
python3 /Applications/stock_dashboard/scratch/financial_4way_revalidate_and_queue.py \
  --revalidate --run-id 4way_full_20260525_01

# Seibro 확장(현재는 P&L action 제약으로 실패 가능)
python3 /Applications/stock_dashboard/scratch/financial_4way_revalidate_and_queue.py \
  --seibro-expand --max-stocks 200 --sleep 0.15

# 수정큐 건수 확인
sqlite3 /Applications/stock_dashboard/stock.db \
"SELECT run_id, fix_rule, COUNT(*) FROM financial_fix_log WHERE run_id='4way_full_20260525_01' GROUP BY run_id, fix_rule;"
```

---
본 문서는 "외부 2소스 일치만으로 자동확정 금지" 원칙에 맞춰,
DART anchor 기반으로 충돌을 큐잉/분석하는 운영 기준을 반영함.
