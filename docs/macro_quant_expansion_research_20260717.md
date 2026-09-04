# Macro/Quant Expansion Research

- 작성: 2026-07-17 17:02:39
- DB: `/Applications/stock_dashboard/stock.db`

## Coverage Summary

| catalog_total | macro_catalog | macro_sector_mapped | macro_direction_ruled | macro_stock_mapped | macro_stock_candidates | macro_signal_events |
| --- | --- | --- | --- | --- | --- | --- |
| 277 | 97 | 97 | 97 | 52 | 936 | 32 |

## Sector Direction Coverage

| sector_name | mapped | with_rule |
| --- | --- | --- |
| 한국 경기 | 15 | 15 |
| 금융 | 12 | 12 |
| 유럽/일본 매크로 | 12 | 12 |
| 글로벌 경기/무역 | 9 | 9 |
| 미국 매크로 | 9 | 9 |
| 시장 레짐 | 9 | 9 |
| 음식료 | 9 | 9 |
| 환율/수출주 | 9 | 9 |
| 반도체 | 8 | 8 |
| 건설/건자재 | 6 | 6 |
| 자동차 | 6 | 6 |
| 철강/비철 | 6 | 6 |
| SW/AI | 5 | 5 |
| 바이오 | 5 | 5 |
| 정유/화학 | 5 | 5 |
| 중국 매크로 | 5 | 5 |
| 기계 | 4 | 4 |
| 리츠/부동산 | 4 | 4 |
| 여행/레저 | 4 | 4 |
| 유통 | 4 | 4 |
| 조선/해운 | 4 | 4 |
| 전력기기 | 3 | 3 |

## Remaining Gaps

### Unmapped Macro Indicators

_없음_

### Mapped But Missing Direction Rule

_없음_

## Stock Mapping Status

| mapping_status | importance_level | count |
| --- | --- | --- |
| candidate_macro_context | unknown_macro_sensitive | 936 |
| candidate_context | unknown | 481 |
| candidate_context | unknown_core_candidate | 101 |
| candidate_context | unknown_cost_sensitive | 78 |
| confirmed_exposure | high | 14 |
| confirmed_relationship | unknown_core_candidate | 8 |
| confirmed_exposure | medium | 1 |

## Research Notes

- `candidate_macro_context`는 종목 페이지 맥락/설명에는 표시할 수 있지만, 자동 매수 신호 점수에는 바로 포함하지 않는다.
- 매수/매도 신호로 승격하려면 지표별 발표일 기준 +20/+60/+120거래일 수익률, 섹터 대비 초과수익, 하락위험을 검증해야 한다.
- 비용성 지표는 매출 노출보다 원가/이익 노출이 중요하므로 `segment_revenue`, `cost_structure`, `dart_material_purchase`로 노출 비중을 먼저 보강한다.
- 다음 개선 과제: macro 후보 936건을 지표-섹터 단위로 백테스트하여 profit factor, hit rate, max drawdown 기준을 통과한 것만 `confirmed_macro_signal`로 승격한다.
