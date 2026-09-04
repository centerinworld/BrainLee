# Codex Handoff — 수주잔고 커버리지 보강 점검 (2026-07-26)

## 요약

사용자 지적: "특정 항목이 34% 수준"은 감가상각이 아니라 수주잔고로 보임.

재점검 결과 `order_backlog` 직접 커버리지는 현재 전체 유니버스 기준 1,257/2,693종목, 약 46.7%다. 과거 문서의 34%보다 이미 개선되어 있다. 다만 수주잔고는 금융/리츠/유통/소비재/바이오 등 많은 업종에서 원천적으로 공시하지 않는 항목이므로 전체 종목 분모만으로 보면 낮게 보인다.

## 현재 커버리지

2026-07-26 DB 기준:

| 범위 | 커버리지 |
| --- | ---: |
| `order_backlog` 직접값 | 1,257 / 2,693 = 46.7% |
| `order_backlog` + `order_contracts` 직접/공시 | 1,559 / 2,693 = 57.9% |
| 수주잔고/수주공시/재고수주 proxy/계약부채 proxy 중 하나 이상 | 1,563 / 2,693 = 58.0% |
| 핵심 수주 가능 업종(산업재/에너지/IT/소재 등) 직접값 | 833 / 1,400 = 59.5% |
| 핵심 수주 가능 업종 + 수주공시 | 975 / 1,400 = 69.6% |
| 핵심 수주 가능 업종 + proxy 포함 | 976 / 1,400 = 69.7% |

## 이번 수정

1. `collectors/dart_backlog_collector.py`
   - `--missing-only` 추가: 이미 수주잔고가 있는 종목은 건너뛰고 미포착 종목만 재파싱.
   - `--eligible-only` 추가: 산업재/에너지/IT/소재 및 자본재/반도체/하드웨어/디스플레이/장비/건설/조선/플랜트/방산 중심으로 재시도.
   - `--annual-only` 추가: 수주잔고가 상대적으로 잘 나오는 사업보고서만 우선 재파싱.

2. `scheduler.py`
   - 주간 DART 수주잔고 수집 범위를 최근 5년(`y_to - 5`)에서 2020년 이후로 고정.
   - 정기 전체 수집 후 `missing_only + eligible_only` 재시도를 추가.

3. 프론트 문구 정정
   - 감가상각 34% 문구는 실제 최신 DB 기준과 맞지 않아 최신 수치로 정정했다. 이번 사용자 재지적에 따라 수주잔고는 별도 문서로 정리.

## 실제 재수집 테스트

명령:

```bash
/Applications/stock_dashboard/venv/bin/python -m collectors.dart_backlog_collector --year-from 2020 --year-to 2026 --missing-only --eligible-only --limit 100
/Applications/stock_dashboard/venv/bin/python -m collectors.dart_backlog_collector --year-from 2020 --year-to 2026 --missing-only --eligible-only --annual-only --limit 100
```

결과:

- 최신 보고서 100건: `ok=1`, `no_metric=99`
- 사업보고서 100건: `ok=0`, `no_metric=100`

해석: 남은 미포착 종목 상당수는 DART 원문에 수주잔고 항목 자체가 없거나, 수주잔고라는 이름으로 공시하지 않는 회사다. 무제한 재수집을 돌려도 전체 커버리지가 크게 오르지는 않을 가능성이 높다.

## Claude 검증 포인트

1. `eligible_only` 업종 필터가 너무 넓거나 좁지 않은지 확인.
2. 수주잔고 직접값이 없는 종목은 매수 로직에서 결측을 0으로 오해하지 말고 `unknown/not_applicable`로 분리.
3. 수주 관련 신호는 아래 우선순위로 사용하는 것이 안전:
   - 1순위: `order_backlog` 직접 수주잔고
   - 2순위: `order_contracts` 최근 대형 수주공시
   - 3순위: `inventory_sales_signals`의 수주/재고 proxy
   - 4순위: `contract_advance_signals` 계약부채/선수금 proxy
4. 과거 백테스트에서 수주잔고 결측 종목을 일괄 0점 처리하면 업종 편향이 생긴다. 수주잔고 가능 업종과 비가능 업종을 나누어 평가해야 한다.
5. `order_backlog` 직접값 기반 전략은 전체 시장 랭킹용보다는 산업재/장비/조선/건설/방산/반도체장비 보조 근거로 쓰는 것이 맞다.
