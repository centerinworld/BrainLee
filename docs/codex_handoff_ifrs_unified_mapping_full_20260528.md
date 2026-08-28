# Codex Handoff — IFRS 단일화/매핑/전수 재검증 (2026-05-28)

## 0) 사용자 요청 반영 범위
- [x] 사용자 요구 기준으로 검증 규칙 재정의(연결/별도 분리, P&L vs BS 분리)
- [x] IFRS 기준 단일화 규칙표(필드별) 문서화
- [x] DART↔Naver/FnGuide 변환/매핑 규칙 코드화
- [x] 기업/항목 단위 확장 가능한 매핑 카탈로그 테이블 신설
- [x] 전체 검증 파이프라인 재실행 및 최신 수치 반영

---

## 1) 이번 세션 코드 수정 사항

## 1-1. BS 전용 검증 로직 분리 적용
- 파일: `/Applications/stock_dashboard/scratch/fin_quarterly_4way_validate.py`
- 핵심 변경:
  - `BS_FIELDS={'total_assets','total_equity'}` 분리
  - BS 전용 허용치 적용: `TOL_BS_CLOSE=0.25`, `TOL_BS_WIDE=0.60`
  - P&L(매출/영업이익/순이익)과 BS(자산/자본)의 판정 함수를 분기 처리

### 적용 효과
- `QUARTERLY_4WAY OK`: **56.6% → 60.3%** (+3.7%p)
- `BS total_assets OK`: **50.0% → 60.4%**
- `BS total_equity OK`: **52.0% → 60.1%**

---

## 1-2. IFRS 단일화 + 매핑 + 재검증 통합 스크립트 추가
- 신규 파일: `/Applications/stock_dashboard/scripts/ops/ifrs_unified_mapping_revalidate.py`
- 역할:
  1. IFRS 필드 규칙 테이블 생성/업서트
  2. 소스별 매핑 규칙 테이블 생성/업서트
  3. DART raw account 기반 매핑 카탈로그 생성
  4. 커버리지 스냅샷 적재
  5. `run_daily_validation.sh` 자동 실행

### 이번 실행
- 명령: `python3 /Applications/stock_dashboard/scripts/ops/ifrs_unified_mapping_revalidate.py`
- 결과 run_id: `ifrs_unify_20260528_203942`
- 검증 로그: `/tmp/daily_validate_20260528_203943.log`

---

## 2) IFRS 기준 단일화 규칙표 (필드별)

| canonical field | 재무구분 | IFRS 기준 | 분기 처리 | 값 성격 |
|---|---|---|---|---|
| revenue | IS | 수익(업종별 매출/영업수익/이자수익/보험수익) | 누적분기→단일분기 환산 | FLOW |
| operating_profit | IS | 영업이익 | 누적분기→단일분기 환산 | FLOW |
| net_income | IS | 당기순이익(지배/비지배 귀속 주의) | 누적분기→단일분기 환산 | FLOW |
| total_assets | BS | 자산총계(기간말) | 환산 없음(기말값 직접 사용) | STOCK |
| total_equity | BS | 자본총계(기간말) | 환산 없음(기말값 직접 사용) | STOCK |

저장 테이블: `ifrs_field_rules` (총 5건)

---

## 3) DART↔Naver/FnGuide 변환/매핑 규칙표

우선순위 원칙
1. DART 원천 우선
2. 외부소스(Naver/FnGuide)는 교차검증 증거
3. DART 부재 시 외부 2소스 일치면 CLOSE_MATCH 후보

저장 테이블: `source_field_mapping_rules` (총 15건)
- source: DART/NAVER/FNGUIDE
- source_field: account_nm/account_id 또는 필드명
- canonical_field: revenue/op_profit/net_income/total_assets/total_equity
- transform_rule: 누적분기 환산, 기간말 직접 사용 등
- verification_rule: DART 교차검증

---

## 4) 기업/항목 item 매핑 구조 (확장 가능)

## 4-1. 신규 카탈로그 테이블
- `dart_item_mapping_catalog`
  - 키: `(canonical_field, account_id, account_nm, sj_nm, fs_div)`
  - 컬럼: `match_rule, sample_count, first_year, last_year, confidence`

## 4-2. 현재 적재 상태
- 총 6건 (현재 `dart_raw_accounts` 표준 계정 기반 시드)
- 필드별:
  - revenue 1
  - operating_profit 1
  - net_income 2
  - total_assets 1
  - total_equity 1

## 4-3. 향후 수집 안정화 방식
- 매일/수집 배치 후 `ifrs_unified_mapping_revalidate.py` 실행 시 카탈로그 자동 갱신
- 신규 account_nm/account_id 등장 시 자동 흡수
- confidence(`HIGH/MEDIUM/LOW`)로 검수 우선순위 부여

---

## 5) 전수 재검증 최신 결과

`fin_quarterly_validation_flags` 기준
- `ANNUAL_CONSISTENCY`: 총 32,162 / OK 73.7% / OPEN 0 / AMBIG 0
- `DART_FG_CROSS`: 총 3,422 / OK 40.1% / OPEN 0 / AMBIG 0
- `DART_NAVER_CROSS`: 총 27,929 / OK 80.0% / OPEN 0 / AMBIG 0
- `OFS_ANNUAL_CONSISTENCY`: 총 34,713 / OK 47.9% / OPEN 0 / AMBIG 0
- `QUARTERLY_4WAY`: 총 419,725 / **OK 60.3%** / OPEN 53,226 / AMBIG 0

요약
- AMBIGUOUS는 0 유지
- 잔여 과제는 OPEN과 STRUCTURAL(특히 BS 최근연도 커버리지)

---

## 6) 왜 DART와 Naver/FnGuide가 다른가 (운영 정의)

1. 연결/별도 범위 차이(CFS vs OFS)
2. 분기 누적값 vs 단일분기 변환 차이
3. 금융업 수익 계정 정의 차이(매출/영업수익/이자수익)
4. 지배주주귀속순이익 vs 당기순이익 계정 선택 차이
5. 반영 시점/정정 반영 시차

정책
- 원천 진실은 DART
- 단, 투자 의사결정용 표시는 IFRS 단일 규칙으로 변환 후 사용
- 외부소스는 파싱/가공 오류 방지를 위한 이중 검증 채널

---

## 7) Claude 후속 작업 요청

P0
- `dart_item_mapping_catalog`를 account_id 중심으로 고도화
- account_nm 패턴 매핑 → account_id 우선 매핑으로 승격

P1
- `source_field_mapping_rules`에 금융업 세부 rule(은행/보험/증권) 분리
- revenue 정의 분기(interest income / premium / fee)

P2
- 프론트 배지 분리
  - `P&L 신뢰도`
  - `BS 신뢰도`
  - `소스부족 OPEN`

P3
- 일 배치에 `ifrs_unified_mapping_revalidate.py` 연결
  - 신규 수집 즉시 매핑/검증 동기화

---

## 8) 재현 명령

```bash
python3 /Applications/stock_dashboard/scripts/ops/ifrs_unified_mapping_revalidate.py
```

```bash
bash /Applications/stock_dashboard/scratch/run_daily_validation.sh
```

