# Codex Handoff — 재무제표/현금흐름표 정밀 보정 + 4중 검증

작성일: 2026-05-24  
작성자: Codex

## 1) 이번에 실제 수정한 코드

### A. 재무 upsert 키 충돌 버그 수정
- 파일: `/Applications/stock_dashboard/crud.py`
- 변경 내용:
  - 기존 조회 키: `(stock_code, year, quarter)`
  - 수정 조회 키: `(stock_code, year, quarter, is_annual, report_type)`
- 목적:
  - 연간/분기, CFS/OFS 레코드가 서로 덮어써지는 구조 차단

### B. DART 계정명 오매핑 방지
- 파일: `/Applications/stock_dashboard/main.py`
- 변경 내용:
  - `부채와자본총계`, `부채및자본총계` 계정은 BS 매핑에서 제외
  - `자산총계/부채총계/자본총계`는 완전 일치 기준으로만 매핑
- 목적:
  - 자산/부채/자본 동일값 오입력 방지

## 2) ID 추적 로그(감사 추적) 적용

### A. 신규 로그 테이블
- `financial_fix_log`
- `cashflow_fix_log`

### B. 보정 스크립트
- 파일: `/Applications/stock_dashboard/scripts/ops/repair_financial_cashflow_with_logs.py`
- 실행 결과 파일:
  - `/Applications/stock_dashboard/scratch/financial_cashflow_repair_20260524_103849.json`

### C. 보정 결과(실행본)
- `financial_fix_log` 누적: **65,317건**
- `cashflow_fix_log` 누적: **0건** (이번 룰은 CF 숫자형 이상탐지 전용)
- 보정 전/후 핵심 변화(CFS 기준):
  - 분기(is_annual=0)
    - `assets_eq_liab`: 48,021 → 808
    - `identity_mismatch`: 66,417 → 12,927
  - 연간(is_annual=1)
    - `assets_eq_liab`: 7,295 → 40
    - `identity_mismatch`: 10,993 → 3,531

## 3) 4중 검증(1:1 비교 포함)

### A. 검증 스크립트
- 파일: `/Applications/stock_dashboard/scripts/ops/run_4layer_fin_cf_validation.py`
- 결과 파일:
  - `/Applications/stock_dashboard/scratch/four_layer_validation_20260524_104042.json`

### B. 검증 결과 요약
1. Layer1 (키 무결성)
- financial dup key: **0**
- cashflow dup key: **0**

2. Layer2 (핵심 필드 충족)
- financial core null rows: **12,368**
- cashflow core null rows: **9,581**

3. Layer3 (회계식 검증)
- identity mismatch: **16,458**
- suspicious `assets==liabilities`: **848**
- suspicious `assets==equity`: **2**

4. Layer4 (소스 1:1 대조, latest snapshot 기준)
- financial compare rows: **17,392**
- financial mismatch rows: **14,474**
- cashflow compare rows: **17,373**
- cashflow mismatch rows: **5,752**

## 4) 삼성전자 샘플 상태

- `financial_data` (CFS)에서 삼성(005930) 기준:
  - 전체 54건 중 BS 핵심 3필드 NULL: 각 10건
- `cash_flow_data` (CFS)에서 삼성 기준:
  - 전체 55건 중 OCF/ICF/FCF NULL: 각 9건

해석:
- 이번 작업으로 “잘못된 값” 대량 보정은 진행됨
- 하지만 “원천 데이터 자체 부재/불완전” 구간은 추가 수집/재파싱 필요

## 5) Claude 재검토 요청 포인트

1. **필수 재수집 우선순위**
- Layer2 NULL 잔여와 Layer4 mismatch 상위 종목부터 재수집
- 권장: 대형주/실매매 영향 상위 universe 우선

2. **4중 검증 자동 파이프라인화**
- 수집 배치 후 아래 2개 스크립트 자동 실행
  - `repair_financial_cashflow_with_logs.py`
  - `run_4layer_fin_cf_validation.py`
- 결과 JSON을 `docs/` 요약본으로 매일 생성

3. **정책 확인 필요 사항**
- Layer4 mismatch는 단순 오차가 아니라 소스간 정의 차이 가능성 있음
- 소스 우선순위(CFS/OFS, DART/FnGuide) 정책을 명문화 후 적용 필요

## 6) 이번 실행 명령 (재현용)

```bash
cd /Applications/stock_dashboard
python3 scripts/ops/repair_financial_cashflow_with_logs.py --dry-run
python3 scripts/ops/repair_financial_cashflow_with_logs.py
python3 scripts/ops/run_4layer_fin_cf_validation.py
```

## 7) 결론

- “오매핑/덮어쓰기”를 유발하던 코드 경로를 차단했고,
- ID 단위 감사추적 로그를 남기며 대량 보정을 완료함.
- 다만 아직 NULL/mismatch 잔량이 크므로, **재수집 + 소스정합 정책 확정**을 Claude가 이어서 검증/보완해야 함.
