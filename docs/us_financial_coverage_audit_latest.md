# 미국 재무제표/현금흐름 커버리지 감사 (자동 생성)

- 생성 시각: 2026-09-27T21:29:55.264123+00:00
- 미국 종목 유니버스: 3676개

## `us_financial_data`
- **annual**: 3337개 종목에 행 존재(90.78%), 4개 이상 기간 확보 2973개, 최신 period_end=2026-05-31
  - `revenue` NULL 비율 30.6% (행 22775개 중)
  - `operating_income` NULL 비율 20.08% (행 22775개 중)
  - `net_income` NULL 비율 5.56% (행 22775개 중)
  - `eps` NULL 비율 61.56% (행 22775개 중)
  - `bps` NULL 비율 84.57% (행 22775개 중)
- **quarter**: 3218개 종목에 행 존재(87.54%), 4개 이상 기간 확보 3086개, 최신 period_end=2026-07-31
  - `revenue` NULL 비율 52.0% (행 55855개 중)
  - `operating_income` NULL 비율 35.35% (행 55855개 중)
  - `net_income` NULL 비율 24.54% (행 55855개 중)
  - `eps` NULL 비율 78.64% (행 55855개 중)
  - `bps` NULL 비율 93.11% (행 55855개 중)

## `us_cashflow_data`
- **annual**: 3335개 종목에 행 존재(90.72%), 4개 이상 기간 확보 2966개, 최신 period_end=2026-05-31
  - `operating_cf` NULL 비율 10.85% (행 22804개 중)
  - `investing_cf` NULL 비율 14.01% (행 22804개 중)
  - `financing_cf` NULL 비율 11.24% (행 22804개 중)
  - `capex` NULL 비율 24.82% (행 22804개 중)
  - `free_cf` NULL 비율 25.46% (행 22804개 중)
- **quarter**: 3199개 종목에 행 존재(87.02%), 4개 이상 기간 확보 3068개, 최신 period_end=2026-07-31
  - `operating_cf` NULL 비율 23.76% (행 55815개 중)
  - `investing_cf` NULL 비율 27.76% (행 55815개 중)
  - `financing_cf` NULL 비율 25.34% (행 55815개 중)
  - `capex` NULL 비율 39.62% (행 55815개 중)
  - `free_cf` NULL 비율 40.23% (행 55815개 중)

## 상세페이지 최소 기준(annual 4건) 미달 표본 (최대 20개)

| ticker | company_name | annual_rows | cf_annual_rows |
|---|---|---:|---:|
| AACB | Artius II Acquisition Inc. | 2 | 2 |
| AACI | Armada Acquisition Corp. III | 1 | 1 |
| AACO | Abony Acquisition Corp. I | 1 | 1 |
| ABVE | Above Food Ingredients Inc. | 3 | 3 |
| ACAA | Averin Capital Acquisition Corp | 0 | 0 |
| ACCL | Acco Group Holdings Limited | 1 | 1 |
| ADAC | American Drive Acquisition Comp | 1 | 1 |
| AEAQ | Activate Energy Acquisition Cor | 1 | 1 |
| AEBI | Aebi Schmidt Holding AG | 3 | 3 |
| AFJK | Aimei Health Technology Co., Lt | 3 | 3 |
| AFJKR | Aimei Health Technology Co., Lt | 3 | 3 |
| AGCC | Agencia Comercial Spirits Ltd | 3 | 3 |
| AGRZ | Agroz Inc. | 3 | 3 |
| AIAI | AIAI Holdings Corporation | 2 | 1 |
| AIDX | 20/20 Biolabs, Inc. | 3 | 3 |
| AIIR | Air Global PLC Ordinary Shares | 3 | 3 |
| ALDF | Aldel Financial II Inc. | 2 | 2 |
| ALF | Centurion Acquisition Corp. | 2 | 2 |
| ALIS | Calisa Acquisition Corp | 2 | 2 |
| ALISR | Calisa Acquisition Corp | 2 | 2 |

> 온디맨드 수집 방식(main.py get_us_stock_detail: annual<4 또는 quarter<4 또는 가격<120행이면 그 자리에서 _refresh_us_stock_data 실행)이라 이 표는 특정 시점 스냅샷일 뿐이며, 종목 상세를 한 번이라도 열면 그 티커의 커버리지가 바뀔 수 있다.