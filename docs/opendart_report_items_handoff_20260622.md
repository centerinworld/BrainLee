# OpenDART 사업/분기보고서 확장 항목 수집 핸드오프

작성: 2026-06-22 06:30 KST

## 목적

기존 OpenDART 수집은 재무제표 핵심값, 매입재료비, 수주잔고, 세그먼트 매출 중심이었다. 텐버거 후보 탐지에 필요한 사업보고서/분기보고서 내 운전자본, 설비투자, 연구개발, 재고, 계약부채 계열 항목을 추가 수집한다.

## 추가된 수집기

- 파일: `scripts/collect_dart_report_items.py`
- 백필 연결: `scripts/run_dart_2020_backfill_all.sh`의 `09_report_items_2020_2026` 단계
- 기본 수집 범위: 2020~현재, 1분기/반기/3분기/사업보고서, CFS 우선 후 OFS 보완

## 신규 저장 테이블

`dart_report_items_quarterly`

주요 컬럼:

- `stock_code`, `corp_code`
- `fiscal_year`, `fiscal_quarter`
- `reprt_code`, `fs_div`
- `metric_name`
- `account_id`, `account_nm`, `sj_div`, `sj_nm`
- `value`, `rcept_no`, `updated_at`

호환 저장:

- 일부 BS 항목은 기존 `dart_bs_items`에도 `item_key` 기준으로 보조 저장한다.

## 추가 수집 metric_name

- `trade_receivable`: 매출채권
- `inventory_assets`: 재고자산
- `trade_payable`: 매입채무
- `contract_assets`: 계약자산 / 미청구공사
- `contract_liabilities`: 계약부채 / 초과청구공사
- `advances_received`: 선수금 / 선수수익
- `short_term_borrowings`: 단기차입금 / 유동성 차입금
- `long_term_borrowings`: 장기차입금 / 사채
- `property_plant_equipment`: 유형자산
- `construction_in_progress`: 건설중인자산
- `intangible_assets`: 무형자산 / 개발비
- `right_of_use_assets`: 사용권자산
- `provisions`: 충당부채
- `research_development_expense`: 연구개발비
- `sga_expense`: 판관비
- `advertising_expense`: 광고선전비 / 판매촉진비
- `depreciation_amortization_expense`: 감가상각비 / 상각비
- `capex_ppe_purchase`: 유형자산 취득
- `capex_intangible_purchase`: 무형자산 취득
- `inventory_write_down`: 재고자산평가손실

## 검증 실행 결과

명령:

```bash
venv/bin/python scripts/collect_dart_report_items.py --limit 30 --years 2024 2025 --reports 11013 11012 11014 11011 --fs-divs CFS OFS --sleep 0.05
```

결과:

- 이번 실행 저장: 3,433건
- 대상: 시가총액 상위 30개 종목, 2024~2025년
- `corp_code` 미확인: 1개

항목별 누적:

| metric_name | rows | stocks | years |
|---|---:|---:|---|
| inventory_assets | 565 | 30 | 2024-2025 |
| provisions | 341 | 26 | 2024-2025 |
| trade_receivable | 287 | 23 | 2024-2025 |
| contract_liabilities | 245 | 15 | 2024-2025 |
| long_term_borrowings | 241 | 25 | 2024-2025 |
| capex_intangible_purchase | 217 | 28 | 2024-2025 |
| capex_ppe_purchase | 217 | 28 | 2024-2025 |
| intangible_assets | 217 | 28 | 2024-2025 |
| property_plant_equipment | 217 | 28 | 2024-2025 |
| contract_assets | 213 | 15 | 2024-2025 |
| trade_payable | 178 | 23 | 2024-2025 |
| right_of_use_assets | 129 | 17 | 2024-2025 |
| short_term_borrowings | 124 | 14 | 2024-2025 |
| sga_expense | 80 | 10 | 2024-2025 |
| depreciation_amortization_expense | 74 | 5 | 2024-2025 |
| advances_received | 73 | 5 | 2024-2025 |
| research_development_expense | 16 | 2 | 2024-2025 |
| advertising_expense | 8 | 1 | 2024-2025 |

## 표본 확인

에이팩트 `200470` 2024년 사업보고서에서 다음 항목이 저장됨:

- 재고자산
- 매출채권
- 매입채무
- 단기/장기차입금
- 유형자산
- 무형자산
- 사용권자산
- 충당부채
- 유형자산 취득 CAPEX
- 무형자산 취득 CAPEX

## 클로드 검증 포인트

1. `dart_report_items_quarterly`에서 동일 metric이 복수 계정으로 잡히는 경우 집계 정책을 결정해야 한다.
   - 예: 매출채권 및 기타채권, 장기매출채권 등
   - 현재는 원천 계정 단위로 보존한다.
2. `dart_bs_items` 호환 저장은 기존 로직용 보조 테이블이다. 신규 분석은 `dart_report_items_quarterly`를 우선 사용해야 한다.
3. R&D/광고비는 기업별 계정명이 다양해 coverage가 낮다. 추후 `dart_item_mapping_catalog`의 실제 account_nm 빈도 기반으로 키워드를 보강해야 한다.
4. 전 종목/전연도 백필은 `scripts/run_dart_2020_backfill_all.sh` 또는 아래 명령으로 실행 가능하다.

```bash
venv/bin/python scripts/collect_dart_report_items.py --years 2020 2021 2022 2023 2024 2025 2026 --limit 10000
```
