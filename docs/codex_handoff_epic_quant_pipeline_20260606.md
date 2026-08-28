# EPIC 퀀트지표 수집 준비 핸드오프

작성일: 2026-06-06  
작성자: Codex  
대상: Claude 또는 후속 자동화 에이전트

## 1. 목표

EPIC 사이트에서 수집한 퀀트 투자용 주요지표(자동차 수출입, 철강, 카드결제, 기준금리, 대차잔고 등)를
현재 대시보드/백테스트/종목발굴 로직에 안전하게 연결하기 위한 준비 상태를 점검하고,
즉시 가능한 단계와 추가 인증이 필요한 단계를 분리한다.

이번 점검의 핵심은 아래 두 축이다.

1. `stock.db`의 `forward_strategy_*` 테이블  
   EPIC 산업/선행지표 메타데이터 및 시계열 저장소
2. `hs_trade_lab/data/hs_trade_lab.db`  
   수출입/HS/텔레그램 매핑/카드 캐시 저장소

---

## 2. 현재 확인 결과

### 2-1. EPIC 관련 로컬 자산

위치: `/Applications/stock_dashboard/scratch/epic`

- `t3e.json` 존재
- `r3e.json` 존재
- `ui_crawl_latest.json` 존재

확인 요약

- `t3e.json`: 리스트 24개
- `r3e.json`: `recentIndustryCategories` 포함
- `ui_crawl_latest.json`: 93개 indicator item

즉, **로그인된 브라우저 UI에서 긁어온 메타/화면 레벨 자료는 확보되어 있음**.

### 2-2. DB 적재 상태

`stock.db`

- `forward_strategy_industry_categories = 43`
- `forward_strategy_indicators = 93`
- `forward_strategy_indicator_series = 0`
- `forward_strategy_related_companies = 711`
- `forward_strategy_raw_responses = 97`

해석:

- 카테고리/지표 목록/관련기업은 적재됨
- **시계열 값(series)은 아직 0건**
- 현재 indicator source는 전부
  `EPIC authenticated browser UI crawl; token-free screen/DOM capture.`

즉, **EPIC 지표의 “무엇을 가져올지” 목록과 종목 연결은 준비되었지만,
실제 차트 시계열 값은 아직 인증 토큰 기반 본수집이 필요**하다.

### 2-3. HS Trade Lab 캐시 상태

`hs_trade_lab/data/hs_trade_lab.db`

- `analysis2_company_hs_monthly_cache = 96,890`
- `analysis2_sector_hs_monthly_cache = 28,789`
- `hs_code_company_map = 978`
- `hs_sector_map = 303`
- `telegram_company_hs_flow_map = 37,233`
- `telegram_post_cache = 16,319`
- `telegram_trade_card = 949`
- `trade_series_cache = 18,119`
- `sigungu_trade_record = 2,748`

해석:

- 자동차/철강/반도체 등 수출입 관련 내부 캐시는 이미 꽤 잘 쌓여 있음
- EPIC 지표와 별개로, **수출입 기반 quant signal은 현재 DB만으로도 상당 부분 활용 가능**

---

## 3. 이번에 사용/검증한 스크립트

### 3-1. 핵심 스크립트

- `/Applications/stock_dashboard/scripts/sync_epic_forward_strategy.py`
- `/Applications/stock_dashboard/scripts/ops/prepare_epic_quant_pipeline.py`

### 3-2. 보강한 내용

`prepare_epic_quant_pipeline.py`에 아래 기능을 추가했다.

1. `stock.db`의 `forward_strategy_*` 상태 스냅샷
2. `hs_trade_lab.db`의 HS/텔레그램 캐시 상태 스냅샷
3. readiness 평가
   - `not_ready`
   - `metadata_ready`
   - `timeseries_ready`
4. gap 목록 자동 산출
5. 다음 실행 권장 액션 자동 제안

---

## 4. 실제 실행 결과

실행 명령:

```bash
cd /Applications/stock_dashboard
./venv/bin/python scripts/ops/prepare_epic_quant_pipeline.py --apply-local --smoke-network
```

결과 리포트:

- `/Applications/stock_dashboard/scratch/epic/epic_quant_pipeline_report_20260606_220429.json`

핵심 결과:

- `epic_pipeline_level = metadata_ready`
- `epic_metadata_ready = true`
- `epic_timeseries_ready = false`
- `hs_trade_ready = true`
- `epic_access_token_present = false`

gap:

- `missing_epic_token`
- `missing_epic_series_points`

즉시 권장 next step:

- `EPIC_ACCESS_TOKEN 주입 후 시계열 본수집 실행`

---

## 4-1. EPIC 대체 수집 계획표 생성 결과

EPIC 자체 사용이 불가능한 상황을 대비해, EPIC 지표명을 기준으로
우리 시스템에서 대체 수집 가능한지 자동 분류한 계획표를 만들었다.

생성 스크립트:

- `/Applications/stock_dashboard/scripts/ops/build_epic_replacement_plan.py`

저장 위치:

- DB 테이블: `stock.db.epic_indicator_replacement_plan`
- JSON: `/Applications/stock_dashboard/scratch/epic/epic_indicator_replacement_plan_20260606.json`
- CSV: `/Applications/stock_dashboard/scratch/epic/epic_indicator_replacement_plan_20260606.csv`

분류 결과(80개 지표 기준):

- `ready_existing = 3`
- `ready_existing_partial = 2`
- `derivable_after_new_collector = 1`
- `new_collector_needed = 74`

즉시 활용 가능 지표:

1. `한국은행 기준금리 (월)`  
   - 소스: 한국은행 ECOS  
   - 구현 상태: 기존 수집 있음

2. `국내 주식시장 대차잔고 (월)`  
   - 소스: KRX/공공데이터  
   - 구현 상태: 기존 수집 있음

3. `국내 주식시장 투자자 예탁금, 신용공여 추이 (월)`  
   - 소스: 한국은행 ECOS  
   - 구현 상태: 기존 수집 있음

부분 대체 가능 지표:

1. `베트남 의류, 신발 수출 금액 (월)`
2. `베트남 IT제품 수출 금액 (월)`

위 2개는 관세청 customs + HS 매핑으로 상당 부분 대체 가능하다.
다만 EPIC 원본의 품목 바구니 정의와 완전히 동일한지 추가 검증이 필요하다.

신규 수집기 필요 우선군(P1):

- `글로벌 자동차 판매: 국가별 (월)`
- `한국 자동차 판매: 회사별 (월)`
- `한국 자동차 시장 점유율: 회사별 (월)` → 판매량 수집 후 계산 가능
- `현대차/기아/KG모빌리티 모델별 판매`
- `KG모빌리티 수출 판매: 모델별`
- `한국 후판가격 (주)`

## 4-2. 즉시 대체 가능한 3개 지표 실제 적재 완료

신규 스크립트:

- `/Applications/stock_dashboard/scripts/ops/sync_quant_major_indicators.py`

신규 테이블:

- `stock.db.quant_major_indicator_catalog`
- `stock.db.quant_major_indicator_series`

실행 결과:

- `catalog_seeded = 80`
- `base_rate_rows = 325`
- `liquidity_rows = 424`
- `short_balance_rows = 114`

총 적재 시계열:

- `quant_major_indicator_series = 863`

현재 실제 적재된 핵심 지표:

1. `epic:20:1` — 한국은행 기준금리 (월)
   - series: `base_rate_pct`
   - 소스: `ECOS_722Y001_0101000`

2. `epic:20:99` — 국내 주식시장 투자자 예탁금, 신용공여 추이 (월)
   - series: `customer_deposit_100m`
   - series: `credit_balance_100m`
   - 소스: `ECOS_901Y056_S23A`, `ECOS_901Y056_S23E`

3. `epic:20:22` — 국내 주식시장 대차잔고 (월)
   - series: `borrow_balance_million_krw`
   - series: `borrow_balance_qty`
   - 소스: `short_sell_daily_x_price_history`
   - 계산 방식: 월말 대차잔고주수 × 월말 종가 합산

참고:

- 대차잔고 금액은 원천 테이블 `short_sell_daily.borrow_bal_amt`가 비어 있으므로,
  **주수 × 종가** 방식으로 재계산하여 저장했다.
- 이 방식은 “대차잔고 시장총액 추이”를 만드는 데 실무적으로 유효하며,
  계산 근거도 명확하다.

간단 조회 API도 추가됨:

- `/api/quant-major-indicators/catalog`
- `/api/quant-major-indicators/series/{indicator_key}`
- `/api/quant-major-indicators/summary`

---

## 5. 현재 가능한 것 / 아직 안 되는 것

### 5-1. 지금 가능한 것

1. EPIC 지표 카테고리/이름/주기/단위/관련기업 메타 확인
2. 어떤 지표가 자동차/철강/금리/대차잔고/카드결제와 연결되는지 목록화
3. HS Trade Lab의 수출입 데이터와 EPIC 메타를 결합해 페이지/전략 설계 준비
4. 관련기업 연결(`forward_strategy_related_companies`) 기반 종목 연결 초안 작성

### 5-2. 아직 안 되는 것

1. EPIC 지표의 차트용 시계열 값 적재
2. 시점별 백테스트용 월/주/일 수치 사용
3. “최근 12개월 증가율”, “전월 대비”, “sector regime” 같은 수치 기반 실시간 로직

원인:

- `EPIC_ACCESS_TOKEN` 미주입
- 따라서 `/api/industry/codes/.../chart` 실호출 미실행

---

## 6. 다음 단계 실행 순서

### Step A. EPIC 대체 수집 우선순위 확정

EPIC 자체가 불가하다면, 아래 3개 레이어로 진행하는 것이 현실적이다.

1. **즉시 대체 가능**
   - 기준금리
   - 대차잔고
   - 투자자 예탁금/신용공여

2. **기존 customs/HS로 부분 대체 가능**
   - 자동차 수출입
   - 베트남 의류/IT 수출

3. **신규 수집기 필요**
   - 자동차 판매/점유율/모델별
   - 후판가격
   - 카드업종 소비
   - 쇼핑/관광/교통/카지노 월간 KPI

### Step B. EPIC 인증 토큰 확보 (선택)

환경변수:

```bash
export EPIC_ACCESS_TOKEN="..."
```

또는 `.env`/런타임 환경에 안전하게 주입.

### Step C. 소규모 smoke test

```bash
cd /Applications/stock_dashboard
./venv/bin/python scripts/ops/prepare_epic_quant_pipeline.py --smoke-network
```

기대 결과:

- `forward_strategy_indicator_series > 0`
- `forward_strategy_raw_responses` 증가

### Step D. EPIC 본수집

```bash
cd /Applications/stock_dashboard
./venv/bin/python scripts/sync_epic_forward_strategy.py --max-indicators 20 --delay 0.5
```

주의:

- 처음에는 `--max-indicators 20` 정도로 제한해서 schema drift 여부 먼저 확인
- 정상 확인 후 전체 수집으로 확대

### Step E. HS + 대체지표 결합 규칙 설계

후속 에이전트는 아래 우선순위로 연결하는 것이 좋다.

1. EPIC metadata → indicator catalog
2. HS Trade cache → 실제 수출입 시계열
3. 종목 연결 → `forward_strategy_related_companies` + `hs_code_company_map`
4. 화면/전략 연결 → 시장지표/종목발굴/백테스트

---

## 7. 추천 구현 방향

### 7-1. 단기

가장 먼저 할 것:

1. `epic_indicator_replacement_plan` 기준으로 P1 신규 수집기 범위 확정
2. 이미 있는 지표(ECOS/KRX/customs)를 `major_indicator` 계열 API에 연결
3. 자동차/후판 중 1개 카테고리 수집기부터 신규 구현
4. 이후 필요시 EPIC 토큰 기반 본수집은 보조 경로로만 사용

추천 1차 후보 지표:

- 한국 자동차 판매/시장점유율
- 한국 후판가격
- 국내 주식시장 대차잔고
- 투자자 예탁금/신용공여 추이

### 7-2. 중기

지표를 아래 3개 군으로 나눠 관리 추천.

1. **거시/금융**
   - 기준금리
   - 투자자 예탁금
   - 대차잔고

2. **산업/원자재**
   - 철강 가격
   - 석유 재고
   - Rig Count

3. **소비/실물**
   - 카드결제액
   - 교통이용량
   - 자동차 판매/수출입

### 7-3. 장기

최종 목표는 EPIC 지표와 HS-trade를 합쳐 아래를 만드는 것.

- 산업별 선행지표 대시보드
- 종목별 관련 산업지표 패널
- 텐배거/모멘텀/수출입 전략의 보조 필터
- AI 종목발굴의 섹터 레짐 필터

---

## 8. 다른 AI가 반드시 재확인할 것

1. EPIC `/chart` 응답 포맷이 현재 정규화 로직과 맞는지
2. `dataCode` 별 의미 차이
3. period 포맷
   - `2026.04`
   - `2026.04.29`
   - 주/월 혼재
4. `series_type` 구분 기준
5. 관련기업 응답이 종목코드 기준으로 안정적인지
6. UI crawl 결과와 API 결과 간 indicator 식별자가 일치하는지

---

## 9. 결론

현재 상태를 한 줄로 정리하면:

**EPIC 퀀트지표 수집 파이프라인은 “메타데이터 준비 완료, 시계열 본수집 대기” 상태이며,
EPIC 자체를 못 쓰더라도 `epic_indicator_replacement_plan`을 기준으로 우리 소스로 대체 구현을 시작할 수 있다.**

즉, 지금 당장 할 수 있는 최선의 다음 단계는

1. ECOS/KRX/customs로 즉시 대체 가능한 지표부터 화면/API 연결
2. 자동차 판매/점유율/후판가격 신규 수집기 설계
3. 필요 시에만 EPIC 토큰 기반 시계열을 보조 소스로 재검토

이다.
