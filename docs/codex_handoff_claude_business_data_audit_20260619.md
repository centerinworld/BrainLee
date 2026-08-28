# Codex 핸드오프: Claude 매입재료비/수주잔고/섹터별 매출 분리 감사 (2026-06-19)

## 목적

Claude가 보강한 3개 데이터 축을 퀀트 신호로 투입해도 되는지 점검했다.

- 매입재료비: `dart_material_purchase`, `cost_structure`, `dart_cost_quarterly`
- 수주잔고: `order_backlog`, `dart_backlog_quarterly`
- 기업별 매출 내 섹터/부문 매출 분리: `segment_revenue`

이번 감사는 **읽기 전용 점검**으로 수행했다. DB 값은 수정하지 않았고, 감사 스크립트와 결과 JSON만 생성했다.

## 산출물

- 감사 스크립트: `scripts/audit_claude_business_data.py`
- 감사 결과: `research_outputs/claude_business_data_audit.json`
- 전수 오류 export 스크립트: `scripts/export_claude_business_error_handoff.py`
- Claude 전달용 전수 오류 디렉터리: `research_outputs/claude_business_error_handoff_20260619/`
- 대상 DB: `/Applications/stock_dashboard/stock.db`
- 실행 시각: `2026-06-19T21:19:52+09:00`

검증 명령:

```bash
/Applications/stock_dashboard/venv/bin/python -m py_compile scripts/audit_claude_business_data.py
/Applications/stock_dashboard/venv/bin/python scripts/audit_claude_business_data.py
/Applications/stock_dashboard/venv/bin/python -m py_compile scripts/export_claude_business_error_handoff.py
/Applications/stock_dashboard/venv/bin/python scripts/export_claude_business_error_handoff.py
```

## 최종 결론

| 영역 | 현재 판정 | 전략 투입 여부 |
|---|---|---|
| 연간 매입재료비 `dart_material_purchase` | sanity filter 적용 시 사용 가능 | 제한적으로 가능 |
| `cost_structure` 원재료비 연간 백필 | 대체로 사용 가능하나 ratio 재계산 필요 | ratio 보정 후 가능 |
| 분기 매입재료비 `dart_cost_quarterly.material_cost_krw` | 연도/기수/퍼센트 오파싱 다수 | 현재 사용 금지 |
| 수주잔고 `order_backlog` | 오파싱과 단위 불일치가 남아 있음 | 현재 사용 금지 |
| 수주잔고 원문 근거 `dart_backlog_quarterly` | 원문 증거는 있으나 테이블 파서 재작성 필요 | 원문 검증용만 가능 |
| 섹터/부문 매출 `segment_revenue` | 실제 부문 매출 테이블이 아님 | 현재 사용 금지 |

가장 위험한 부분은 `segment_revenue`다. 현재 테이블은 "기업별 매출 내 섹터별 분리"라고 보기 어렵고, 대부분 `연결전체` 행이며 일부 breakdown 행은 `기타영업수익`, `기타영업외수익` 같은 손익계산서 계정명이 부문명처럼 저장되어 있다. 이 테이블을 텐버거/시장 2배 전략에 넣으면 설명력 있는 신호가 아니라 데이터 오염을 학습할 가능성이 높다.

## Claude 전달용 전수 오류 export

Claude가 바로 수정 작업에 들어갈 수 있도록 row-level CSV를 생성했다.

경로:

```text
/Applications/stock_dashboard/research_outputs/claude_business_error_handoff_20260619/
```

요약 파일:

```text
/Applications/stock_dashboard/research_outputs/claude_business_error_handoff_20260619/summary.json
```

전수 오류 카운트:

| 오류 파일 | 건수 | 의미 |
|---|---:|---|
| `material_annual_outliers.csv` | 0 | 연간 매입재료비 원천값의 극단 outlier |
| `cost_structure_ratio_mismatch.csv` | 753 | `raw_material_ratio != raw_material_cost / revenue` |
| `cost_structure_ratio_out_of_range.csv` | 168 | 원재료비율 음수 또는 300% 초과 |
| `dart_cost_quarterly_bad_values.csv` | 678 | 분기 매입재료비 소액/음수/저신뢰 값 |
| `dart_cost_quarterly_bad_context.csv` | 490 | 분기 매입재료비가 연도/기수/퍼센트 컨텍스트에서 추출된 의심값 |
| `dart_backlog_quarterly_bad_parse.csv` | 951 | 수주잔고 날짜/무의미 문구/소액 오파싱 |
| `order_backlog_unit_mismatch.csv` | 1,062 | `backlog_normalized` 단위 불일치 |
| `order_backlog_ratio_mismatch.csv` | 0 | `backlog_to_rev` 계산식 직접 불일치 |
| `order_backlog_bad_values.csv` | 469 | 수주잔고 소액/음수/비정상 completion/new order |
| `segment_revenue_fake_is_accounts.csv` | 0 | 현재 export 시점의 손익계산서 계정명 segment row |
| `segment_revenue_breakdown_rows.csv` | 0 | 현재 export 시점의 `연결전체` 외 breakdown row |
| `segment_revenue_consolidated_mismatch.csv` | 5,083 | `연결전체` 매출과 `financial_data.revenue` 대조 불일치 |
| `raw_table_usage_in_code.csv` | 100 | dirty raw table을 직접 참조하는 코드 위치 |

주의:

- `segment_revenue_breakdown_rows.csv=0`이라는 점이 중요하다. 현재 DB 기준으로는 Claude의 "섹터별 매출 분리" 결과가 실제 breakdown 데이터로 남아 있지 않다.
- `segment_revenue_consolidated_mismatch.csv=5,083`은 단위/기준/중복 문제를 모두 포함한 경고다. 이 파일은 곧바로 DELETE 대상이 아니라, `financial_data` 자체의 중복/단위 이슈까지 함께 검토해야 하는 대조 목록이다.
- `raw_table_usage_in_code.csv`에는 감사/백필 스크립트도 포함되어 있으므로, 전략 소비 경로를 분리하려면 `discover_market2x_signals.py`, `research_alpha_strategy.py`, `tenbagger_engine.py`, `routes/tenbagger.py`를 우선 확인한다.

## 1. 매입재료비 감사 결과

### 커버리지

| 테이블 | 행 수 | 종목 수 |
|---|---:|---:|
| `dart_material_purchase` | 2,652 | 1,072 |
| `cost_structure` | 27,742 | 2,356 |
| `dart_cost_quarterly` | 37,891 | 2,333 |

`dart_material_purchase` 연도별 커버리지:

| 연도 | 행 수 | 종목 수 | 매입재료비 non-null |
|---|---:|---:|---:|
| 2021 | 1 | 1 | 0 |
| 2022 | 828 | 828 | 797 |
| 2023 | 899 | 899 | 877 |
| 2024 | 924 | 924 | 900 |

`dart_material_purchase.material_purchase_krw` 자체는 이번 감사 기준으로 10억원 미만 또는 50조원 초과 outlier가 0건이었다. 따라서 **연간 원재료 매입액 원천 테이블은 필터를 걸면 쓸 수 있다.**

### 문제 1: `cost_structure.raw_material_ratio`가 기존 오류값을 유지

`cost_structure`의 `raw_material_cost`는 연간 매입재료비 백필로 개선되었지만, 일부 `raw_material_ratio`가 `raw_material_cost / revenue`와 전혀 맞지 않는다.

대표 샘플:

| 종목 | 연도 | raw_material_cost | revenue | 저장 ratio | 기대 ratio |
|---|---:|---:|---:|---:|---:|
| 심텍홀딩스(036710) | 2022 | 531,875,000,000 | 1,710,417,170,744 | 802,938.055 | 0.311 |
| 우리산업홀딩스(072470) | 2022 | 100,564,859,000 | 495,522,375,288 | 231,195.808 | 0.203 |
| 동남합성(023450) | 2022 | 134,500,000,000 | 193,400,000,000 | 11,225.270 | 0.695 |

원인 추정:

- 이전 값이 퍼센트/억원/원 단위 혼합 상태로 저장됨.
- 연간 백필 스크립트가 기존 `raw_material_ratio`를 덮어쓰지 않고 유지한 것으로 보임.

수정 필요:

1. `raw_material_cost > 0 AND revenue > 0`이면 `raw_material_ratio = raw_material_cost / revenue`로 재계산.
2. 계산 ratio가 비현실적이면 NULL 처리. 예: `raw_material_ratio < 0 OR raw_material_ratio > 3`.
3. `raw_material_yoy`도 같은 기준으로 연도별 재계산.

### 문제 2: `dart_cost_quarterly.material_cost_krw`는 현재 사용 금지

분기 매입재료비 테이블은 정규식이 원재료비가 아니라 연도, 기수, 퍼센트, 문장 번호를 잡는 케이스가 많다.

대표 샘플:

- 177350, 2026Q1: `material_cost_krw=2026.0`, 원문에 `2026년 1분기 2025년 2024년`
- 159580, 2026Q1: `material_cost_krw=27.0`, 원문에 `제27기`
- 154030, 2026Q1: `material_cost_krw=100.0`, 원문에 `100%`
- 148930, 2026Q1: `material_cost_krw=30.0`, 원문에 `30~35%`

수정 필요:

- 숫자 주변 컨텍스트에 `년`, `기`, `%`, 날짜 패턴이 있으면 reject.
- 금액 단위가 명시되지 않은 1천만원 미만 값은 reject.
- 테이블 행/열 기반 추출로 바꾸고, 정규식 fallback은 `confidence`를 낮게 저장.
- `confidence >= 0.75` 및 `material_cost_krw >= 10,000,000` 조건을 만족하지 않는 값은 전략에서 제외.

## 2. 수주잔고 감사 결과

### 커버리지

| 테이블 | 행 수 | 종목 수 |
|---|---:|---:|
| `order_backlog` | 6,707 | 838 |
| `dart_backlog_quarterly` | 1,425 | 261 |

### 문제 1: `dart_backlog_quarterly`가 날짜/문장 번호/무의미 문구를 수주잔고로 저장

대표 샘플:

- 069510, 2026Q1: `backlog_amount=2026.04`, 원문에 `CAR용 2026.04`
- 066700, 2026Q1: `backlog_amount=2026.01`, 원문에 `2026.01 ~ 2026.03`
- 060280, 2026Q1: `backlog_amount=5.0`, 원문에 `수주잔고는 의미가 없다고 판단... 5. 위험관리`
- 053690, 2026Q1: `backlog_amount=2.0`, 실제 표 금액 대신 문장/순번을 잡은 것으로 보임.

수정 필요:

- 원문에 `의미가 없습니다`, `해당사항 없음`, `해당사항 없습니다`, `수주잔고는 의미가 없`이 있으면 no_metric으로 분류.
- 날짜 패턴 `20xx.xx`, `20xx년`, `YYYY-MM-DD` 주변 숫자는 reject.
- 테이블 안의 `수주잔고`, `계약잔액`, `잔고`, `미이행` 컬럼만 후보로 인정.
- 수주잔고는 원칙적으로 1천만원 미만 값을 reject하되, 단위 열이 명확할 때만 예외 허용.

### 문제 2: `order_backlog.backlog_normalized` 단위가 섞여 있음

`backlog_amount`와 `backlog_normalized`가 같은 단위 체계로 연결되지 않는 샘플이 다수 발견되었다.

대표 샘플:

| 종목 | 연도/분기 | backlog_amount | 저장 normalized | 기대 million KRW |
|---|---:|---:|---:|---:|
| 삼성SDI(006400) | 2022Q4 | 70,382,426,000,000 | 703,824.3 | 70,382,426.0 |
| 현대로템(064350) | 2025Q1 | 805,573,731,000 | 21,118,700.0 | 805,573.731 |

첫 번째 유형은 `backlog_amount / 1e8`에 가까운 값이 저장되어 있고, 두 번째 유형은 `backlog_amount`와 `normalized`가 서로 다른 원천/개념에서 온 것으로 보인다.

수정 필요:

1. `backlog_normalized`의 의미를 확정한다. 추천: `backlog_million_krw = backlog_amount / 1,000,000`로 새 컬럼 또는 clean view를 만든다.
2. 기존 `backlog_normalized`는 legacy로 취급하거나 전량 재계산한다.
3. `backlog_to_rev`는 `backlog_amount / annual_revenue` 기준으로 다시 만든다.
4. 수주잔고 클린 뷰에는 `source`, `confidence`, `unit`, `raw_excerpt`를 함께 남긴다.

## 3. 섹터/부문 매출 분리 감사 결과

### 커버리지

| 테이블 | 행 수 | 종목 수 |
|---|---:|---:|
| `segment_revenue` | 16,583 | 2,563 |

문제는 행 수가 아니라 의미다. 대부분이 `segment_name='연결전체'`이고, 실제 breakdown으로 보이는 행은 감사 결과 7건뿐이었다. 그 7건도 모두 LG화학(051910) 계열의 손익계산서 계정명으로 보인다.

감사에서 포착된 문제 유형:

- `기타영업수익`
- `기타영업외수익`

이 값들은 사업부문, 제품군, 섹터가 아니라 표준 손익계산서 계정명이다. 따라서 현재 `segment_revenue`는 "기업별 매출 내 섹터별 매출 분리" 작업이 성공한 테이블이 아니다.

### 문제 1: 수집기가 손익계산서 계정을 segment로 저장

현재 `scripts/collect_dart_segment_breakdown.py`는 DART `fnlttSinglAcntAll`에서 손익계산서(IS) 계정을 가져오고, 계정명에 `매출`, `수익`, `Revenue`, `부문` 등이 있으면 segment 후보로 저장하는 구조다. 이 방식은 영업부문 주석 테이블이 아니라 손익계산서 계정을 읽기 때문에, `기타영업수익` 같은 계정이 부문으로 저장될 수 있다.

수정 필요:

- `fnlttSinglAcntAll` 기반 계정명 수집을 부문 매출 파서로 사용하지 않는다.
- DART 사업보고서 주석의 `영업부문`, `사업부문`, `제품과 용역`, `지역별 매출` 표를 별도로 파싱한다.
- 새 테이블 또는 clean view에서 아래 분류를 분리한다.
  - `consolidated_total`
  - `business_segment`
  - `product_segment`
  - `region_segment`
  - `legacy_is_account`

### 문제 2: 단위가 섞여 있음

`연결전체` 행은 대체로 억원 단위로 보이지만, breakdown 샘플은 KRW 원 단위로 저장된 값이 섞여 있다. 이 상태에서는 같은 컬럼의 숫자를 비교할 수 없다.

수정 필요:

- `revenue_amount_krw`와 `revenue_amount_eok`를 분리하거나, `amount_krw` 하나로 통일한다.
- 반드시 `unit_detected`, `unit_multiplier`, `source_table_title`, `raw_cell`을 저장한다.
- `financial_data.revenue`와 비교하는 validation을 저장 직후 실행한다.

## 즉시 적용하면 안 되는 전략 신호

아래 신호는 현재 상태로는 백테스트/실거래 로직에 넣으면 안 된다.

- `dart_cost_quarterly.material_cost_krw` 기반 분기 원재료비 급증률
- `order_backlog.backlog_normalized` 기반 수주잔고 증가율
- `dart_backlog_quarterly.backlog_amount`를 그대로 사용한 수주잔고 factor
- `segment_revenue.segment_name != '연결전체'` 기반 섹터/부문 매출 factor
- `segment_revenue`의 부문별 매출 성장률, 부문 집중도, 신규 부문 매출 등

아래 신호는 보정 후 제한적으로 쓸 수 있다.

- `dart_material_purchase.material_purchase_krw`의 연간 YoY
- `cost_structure.raw_material_cost / revenue`로 재계산한 원재료비 매출비중
- 에이팩트(200470)처럼 연간 원재료비가 명확히 증가한 케이스의 중장기 후행 검증

## 권장 수정 순서

1. `cost_structure` ratio repair 스크립트 작성
   - `raw_material_ratio = raw_material_cost / revenue`
   - ratio 범위 검증
   - `raw_material_yoy` 재계산
   - 적용 전/후 CSV diff 생성

2. 클린 뷰 생성
   - `v_material_purchase_clean`
   - `v_order_backlog_clean`
   - `v_segment_revenue_clean`
   - 기존 raw 테이블은 보존하고 전략은 clean view만 읽게 한다.

3. `dart_cost_quarterly` 매입재료비 파서 재작성
   - 정규식 단독 추출 금지
   - 금액 단위/테이블 헤더/행 이름 검증 필수
   - 날짜/기수/퍼센트 오파싱 reject

4. 수주잔고 파서 재작성
   - 원문 문장 fallback보다 표 기반 추출 우선
   - "해당사항 없음/의미 없음" no_metric 분리
   - `backlog_amount`와 normalized 단위 통일

5. `segment_revenue` 재설계
   - 현재 테이블은 legacy로 격리
   - 실제 영업부문/제품/지역 주석 표에서 새로 수집
   - 손익계산서 계정명은 부문명으로 저장하지 않도록 차단

6. 감사 스크립트 운영화
   - `scripts/audit_claude_business_data.py`를 야간 검증 또는 수집 직후 검증에 연결
   - bad parse 샘플이 0건을 초과하면 strategy factor build를 중단
   - 감사 결과 JSON을 날짜별로 보관

## 다음 담당자 체크리스트

- [ ] `cost_structure.raw_material_ratio` 전수 재계산 스크립트 작성 및 dry-run
- [ ] `dart_cost_quarterly.material_cost_krw < 10,000,000` 또는 날짜/퍼센트 컨텍스트 값 NULL 처리
- [ ] `order_backlog.backlog_normalized` 단위 정의 확정 후 전량 재계산
- [ ] `dart_backlog_quarterly` no_metric 문구와 날짜 오파싱 샘플 제거
- [ ] `segment_revenue` legacy 격리 및 새 segment parser 설계
- [ ] 전략/백테스트 코드가 raw 테이블을 직접 읽는 경로 차단
- [ ] clean view 기준으로 에이팩트(200470) 사례를 재검증

## 운영 메모

이번 감사에서 확인한 결론은 "데이터가 많다"와 "전략에 쓸 수 있다"가 다르다는 점이다. 매입재료비 연간 데이터는 살릴 수 있지만, 분기 매입재료비/수주잔고/부문 매출은 현재 상태로는 강한 신호처럼 보여도 오파싱과 단위 오류가 섞여 있다. 시장평균 2배 이상 전략을 찾기 전에, 이 3개 영역은 raw signal이 아니라 **검증된 clean signal**로 재구축해야 한다.
