# Codex 실행/검증 핸드오프 — FnGuide급 신뢰도 목표 (2026-05-31)

작성자: Codex  
목적: "DART 원천 + IFRS 변환 + FnGuide급 신뢰도" 목표를 기준으로, Codex가 직접 실행한 점검 결과와 Claude 검증 포인트를 분리 전달

---

## 1) 이번 턴에서 Codex가 직접 실행한 내용

### A. 운영 기준 고정 (CLAUDE.md 반영)
- 파일: `/Applications/stock_dashboard/CLAUDE.md`
- 추가 섹션: **FnGuide급 신뢰도 목표 운영 규칙 (상시 고정, 2026-05-31 추가)**
- 핵심 반영:
  - DART raw 원천 보존
  - AI는 후보 제안자 역할만 수행 (자동확정 금지)
  - `account_id + sj_nm + fs_div` 우선, `account_nm` 단독 자동반영 금지
  - CFS/OFS 혼합 금지, Q4 파생 규칙 고정
  - DART 020 상태에서 대량재수집 금지
  - 샘플검증 없이 전종목 UPDATE 금지

### B. 현재 품질 수치 실측 (DB 조회 실행)
실행 시각: 2026-05-31 (KST)

- `cash_flow_data` (CFS 분기, 2022+)
  - total: **37,740**
  - depreciation NULL: **11,593**
  - capex NULL: **10,502**
  - both NULL: **7,952**

- `fin_quarterly_validation_flags` (`QUARTERLY_4WAY`)
  - total: **419,725**
  - confirmed: **31,633**
  - open: **43,895**
  - ambiguous: **0**
  - structural: **89,316**

- `ANNUAL_CONSISTENCY`
  - total: **32,163**
  - confirmed: **24,198**
  - open: **0**
  - ambiguous: **0**
  - structural: **7,901**

- 메타 테이블
  - `dart_raw_accounts`: **112행**
  - `dart_item_mapping_catalog`: **6행**
  - `financial_fix_log` 중 `DART_VERIFIED_QUARTER_SYNC`: **15건**

### C. DART 키 상태 실측 (KEY1/2/3)
실행: `OpenDartReader.finstate_all('005930', 2025, '11011', fs_div='CFS')`

- KEY1: `status=020` (한도초과)
- KEY2: `status=020` (한도초과)
- KEY3: `status=020` (한도초과)

결론: 현재 시점에서 DART 재수집 배치를 실행해도 실수집 불가.

### D. "실제 보강 스크립트 동작 가능성" 점검
- 실행: `scripts/ops/sync_quarterly_verified.py --scope detailed --year 2026 --quarter 1 --limit 1`
- 결과: `inserted=0, updated=0, no_dart=1` (원인: DART 020)

---

## 2) 핵심 진단

### 진단 1 — 지금 즉시 재수집으로 OPEN/STRUCTURAL 해소 불가
- 이유: DART KEY1/2/3 모두 한도초과(020)로 실데이터 fetch 실패.

### 진단 2 — 일부 기존 자동보강 로직은 정확도 리스크 존재
- `scripts/ops/sync_quarterly_verified.py`의 `pick_metrics()`는 `account_nm` 키워드 중심.
- `account_id`/`sj_nm` 강제 필터가 약하여 오탐 반영 가능성 존재.
- 따라서 "바로 전종목 반영"은 금지되어야 함.

### 진단 3 — 현재 mapping/raw 커버리지가 목표 대비 부족
- `dart_raw_accounts=112`, `dart_item_mapping_catalog=6`는 전종목/전계정 커버리지로는 절대 부족.

---

## 3) Codex가 제안하는 실행 순서 (Claude 검증 전제)

### Phase 0. 안전모드 고정 (즉시)
1. `sync_quarterly_verified.py` / `resolve_quarterly_risks_with_dart.py`의 자동 DB 반영 경로를 임시 차단(또는 dry-run 기본화)
2. `account_nm` 단독 매핑 반영 금지 가드 추가

### Phase 1. 파서 강화 (정확도 우선)
1. `collectors/dart_mapping_engine.py`를 단일 기준으로 사용
2. `account_id + sj_nm + fs_div` 3중 조건 매칭 실패 시 review queue로 전송
3. financial/cashflow 모두 `run_id + fix_log` 필수

### Phase 2. 샘플 검증 (최소 10종목)
1. 대형주 5 + 소형주 5
2. 항목별(revenue/op/ni/ocf/icf/fcf/capex/depr) 오차율 측정
3. 기준: 10% 이내 일치율 합격선 미달 시 전종목 반영 금지

### Phase 3. 전량 반영
1. DART 키 한도 정상화 후 실행
2. 배치 실행 전/후 품질 리포트 생성
3. OPEN/STRUCTURAL 감소량을 check_type별로 보고

---

## 4) Claude 검증 체크리스트 (반드시 확인)

### 코드 검증
- [ ] `sync_quarterly_verified.py`가 `account_id/sj_nm/fs_div` 우선으로 변경되었는가
- [ ] `resolve_quarterly_risks_with_dart.py`가 `취득` 키워드 오탐을 차단했는가
- [ ] 자동 update 전에 dry-run diff 출력이 있는가

### 데이터 검증
- [ ] 샘플 10종목에서 항목별 일치율(10% 기준) 보고서가 생성되는가
- [ ] `financial_fix_log`, `cashflow_fix_log`에 run_id/사유/전후값이 누락 없이 남는가
- [ ] CFS/OFS 혼합 반영이 차단되는가

### 운영 검증
- [ ] DART 020 상태에서 배치가 자동 반영 모드로 실행되지 않는가
- [ ] 실패 시 rollback/보류 큐로 전환되는가

---

## 5) 이번 턴에서 "의도적으로 하지 않은 것"
- DART 020 상태에서 강제 재수집/강제 update
- OPEN/STRUCTURAL 수치 개선을 위한 임의치 대체 입력
- account_nm 키워드 기반 대량 자동 반영

---

## 6) 결론
- 목표(=FnGuide급 신뢰도)는 달성 가능하나, 현재 즉시 대량반영은 위험.
- 우선순위는 **수집량 확대보다 파서/검증 게이트 강화**.
- Codex는 기준을 CLAUDE.md에 고정했고, 실행 가능한 검증 절차를 본 문서로 명문화함.

---

## 7) 2026-05-31 추가 실행 반영 (대량수정 실적)

- `repair_financial_cashflow_with_logs.py` 실반영 수행: **9,702 필드 수정**
- `rebuild_canonical_2022_2025.py` 완료: financial 90,297 / cashflow 78,322
- `ifrs_unified_mapping_revalidate.py` 문법 오류 수정 후 파이프라인 재실행 완료
- `rebuild_depr_q_and_cause_report.py` 추가 보정: **1,979건**
- 최신 QUARTERLY_4WAY: total 419,825 / OK 285,823(68.1%) / OPEN 43,969 / STRUCTURAL 90,033 / AMBIG 0
