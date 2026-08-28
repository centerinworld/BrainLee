# Codex Handoff — 교차검증(단위 불일치/로직 오염) 2026-05-23

## 1) 목적 / 범위
- 사용자 첨부 이슈(시총 단위 불일치로 대형주 분류 오류) 유형이 다른 페이지/로직에도 있는지 선제 검증.
- 우선 범위:
  - `stockeasy_logic_validator.py` (전략 검증/매도 튜닝 핵심)
  - `hs_trade_lab` 수출입분석 페이지(프론트/백엔드)
  - `stock_universe.market_cap` 최신행 단위 상태

---

## 2) 핵심 결론

### [P0] `stock_universe.market_cap` 단위가 최신행 기준으로 혼재됨
- 최신행 대다수는 **억원 단위**.
- 그러나 일부 최신행(11개)은 **원 단위처럼 보이는 값**이 남아 있음.
- 이 상태에서 전략 코드가 단위를 가정하면 대형주/수급비율/스코어가 왜곡될 수 있음.

재현 결과:
- latest 종목 총 3,891개
- `market_cap >= 1e8` 값 11개(비정상 후보)
- `between 1e7 and 1e8` 값 2개(경계값)
- 문제 행은 base_date가 `2026-03-26~2026-03-27`에 집중

예시:
- 코오롱ENP `570000000000.0` (2026-03-27)
- 엔에이치스팩30호 `44044000000.0` (2026-03-26)

---

### [P0] `stockeasy_logic_validator.py`에 단위 가정 충돌이 실제 존재

#### A. 대형주 필터 함수 `_stock_signal_ok`
- 위치: `stockeasy_logic_validator.py:853~912`
- 현재 코드:
  - `is_large = mktcap >= 3000` (억원 가정)
- 이 부분 자체는 **억원 기준이면 정상**.
- 첨부 스크린샷에 나온 “3,000억을 300,000,000,000과 비교” 이슈는 현재 파일에서는 재현되지 않음(과거 상태 가능성).

#### B. Momentum/Value 후보 계산 SQL/점수는 원 단위 임계값 사용
- 위치:
  - `stockeasy_logic_validator.py:230` (`lu.mktcap >= 50000000000`)
  - `:303~305` (`5e12`, `1e12`, `3e11`)
  - `:388` (`lu.mktcap >= 100000000000`)
  - `:446~452` (`1e14`, `2e13`, ...)
- 문제:
  - 동일 파일 내 다른 곳은 `mktcap_억`를 사용.
  - 여기만 원 단위 임계값이면, 최신행이 억원 단위일 때 조건이 사실상 비정상 동작.

#### C. `_extract_sell_features` 단위 판별 휴리스틱 경계 오판
- 위치: `stockeasy_logic_validator.py:1549~1556`
- 현재 코드:
  - `mktcap_억 = mktcap if mktcap < 1e7 else mktcap / 1e8`
- 문제:
  - 삼성전자 `17,100,364.9`(억원)처럼 1e7 초과하는 정상 대형주는 **원 단위로 오인**되어 `0.171억`으로 잘못 변환.
  - 결과적으로 `flow_pct_of_mktcap`가 크게 왜곡될 수 있음.

#### D. `mktcap` 원단위 분기와 억단위 분기가 파일 내 공존
- 위치: `stockeasy_logic_validator.py:1741` (`feat["mktcap"] >= 5e12`)
- `feat["mktcap"]`는 `_extract_sell_features`에서 DB raw를 전달하므로 단위 혼재 영향 직격.

---

### [P1] 수출입분석 페이지(HS)에서 점검/보완한 사항
(이번 턴에서 반영됨)

1. `by-product` API에 `period_ym` 실제 반영
- 파일: `hs_trade_lab/app/main.py`
- 영향: 기준월 드롭다운이 실제 데이터 범위를 바꾸도록 수정.

2. 기업 상세 HS 수출/수입 분리 렌더링
- 파일: `frontend/src/App.jsx`
- 영향: 혼합 표로 인한 해석 오류 완화.

3. 폴백 시 기간축 불일치 완화
- 파일: `hs_trade_lab/app/main.py`
- 영향: 기준월 선택 시 다른 기간 데이터가 섞이는 문제 완화.

주의:
- `analysis2_sector_hs_monthly_cache`에는 `flow_type` 컬럼이 없어 폴백에서 import 분리를 합성 로직으로 처리함.

---

## 3) 재현/검증 스크립트 (Claude용)

### 3-1. latest `market_cap` 분포 점검
```sql
WITH latest AS (
  SELECT su.*
  FROM stock_universe su
  JOIN (
    SELECT stock_code, MAX(base_date) md
    FROM stock_universe
    GROUP BY stock_code
  ) t ON t.stock_code=su.stock_code AND t.md=su.base_date
  WHERE su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
)
SELECT
  COUNT(*) total,
  SUM(CASE WHEN market_cap>=1e8 THEN 1 ELSE 0 END) ge1e8,
  SUM(CASE WHEN market_cap BETWEEN 1e7 AND 1e8 THEN 1 ELSE 0 END) btw1e7_1e8,
  MIN(base_date) min_date,
  MAX(base_date) max_date
FROM latest;
```

### 3-2. 이상치 행 확인
```sql
WITH latest AS (
  SELECT su.*
  FROM stock_universe su
  JOIN (
    SELECT stock_code, MAX(base_date) md
    FROM stock_universe
    GROUP BY stock_code
  ) t ON t.stock_code=su.stock_code AND t.md=su.base_date
  WHERE su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
)
SELECT stock_code, stock_name, market_cap, base_date
FROM latest
WHERE market_cap>=1e8
ORDER BY base_date, market_cap DESC;
```

### 3-3. 대형주 샘플 단위 확인
```sql
SELECT stock_code, stock_name, market_cap, base_date
FROM stock_universe
WHERE stock_code IN ('005930','000660','005380','035420','051910')
ORDER BY stock_code, base_date DESC;
```

---

## 4) 권장 수정안 (Claude 작업 우선순위)

### Priority A (필수)
1. `stock_universe.market_cap` 표준 단위 단일화
- 권장: **억원 단위로 통일** + 메타 컬럼(`market_cap_unit`) 또는 변환 배치 로그 보관.
- 혼재 11개 최신행 정규화 우선.

2. `stockeasy_logic_validator.py` 임계값 단위 통일
- Momentum/Value SQL 필터와 size bonus 임계값을 억원 기준으로 재작성.
- 예: 1000억 → `mktcap >= 1000`.

3. `_extract_sell_features` 휴리스틱 제거
- `mktcap < 1e7` 같은 경계값 추정 삭제.
- 단일 단위 원칙으로 직접 사용.

### Priority B (강력 권장)
4. 단위 안전장치 도입
- 공통 헬퍼 함수(예: `normalize_mktcap_uk()`)
- 비정상 범위 감지 시 warning row 적재 + 로직 실행 차단/보수값 적용.

5. 회귀 테스트 추가
- 삼성전자/SK하이닉스가 `is_large=True` 보장.
- 매도 피처 `flow_pct_of_mktcap`가 극단치(예: ±1000%)로 튀지 않는지 assertion.

---

## 5) 사용자가 지적한 케이스와의 정합
- 사용자 첨부 내용:
  - SK하이닉스/삼성생명이 `is_large=False`로 나옴
  - `_stock_signal_ok` 단위 비교 문제
- 현재 코드 HEAD에서는 `_stock_signal_ok` 자체는 억원 비교(`>=3000`)로 보이므로
  - 과거 코드/다른 브랜치에서 재현됐을 가능성 높음.
- 다만 동일 파일 내 다른 구간의 단위 충돌이 실제 존재하므로, 사용자 체감 “곳곳의 이상”은 타당.

---

## 6) 이번 Codex 작업 로그(요약)
- HS 페이지 관련 점검 중 발견한 오류는 일부 수정 반영.
- 전략 검증기(`stockeasy_logic_validator.py`)는 **의도적으로 직접 수정하지 않고**
  - 재현 근거, 영향 범위, 우선순위를 본 문서에 정리.
- Claude가 단위 정규화 + 회귀테스트까지 일괄 진행하는 것이 안전.

