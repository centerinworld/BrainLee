# Codex Handoff to Claude: 2021-2025 Alpha Logic Research

작성일: 2026-06-21
작성자: Codex
대상: Claude
작업 범위: 현재 `/Applications/stock_dashboard/stock.db` 기반으로 2021-01부터 2025-04 전까지 시장수익률을 초과하는 프로그램 매수/매도 후보 로직 개발 및 검증

## 1. 결론 요약

현재까지 가장 좋은 후보는 아래 조합입니다.

- 전략명: `strong_trend__trend_material__plain__top5`
- 리스크 오버레이: `stop20`
- 운용 방식: 월말 신호 확인 후 다음 거래일 시가 매수, 해당 월 말 종가 매도
- 종목 수: 매월 상위 5개 균등 배분
- 예산: 1억원
- 비용 가정: 왕복 수수료/슬리피지 0.35%
- 손절: 보유월 중 일별 종가가 진입가 대비 -20% 이하이면 해당 종가 기준 청산

성과 요약:

| 구분 | 값 |
|---|---:|
| 최종자산 | 251,841,980원 |
| 총수익률 | +151.84% |
| CAGR | +24.27% |
| 활성월 승률 | 60.8% |
| 개별 거래 승률 | 55.3% |
| 최대낙폭 | -24.55% |
| 2021-2023 학습 구간 | +40.67% |
| 2024-2025.03 검증 구간 | +79.03% |
| 손절 작동 비율 | 6.7% |

시장 비교:

| 벤치마크 | 수익률 | 최대낙폭 |
|---|---:|---:|
| KOSPI | -14.10% | -34.62% |
| KOSDAQ | -17.83% | -28.78% |
| 유동 종목 동일가중 | -36.55% | -50.09% |
| 후보 로직 | +151.84% | -24.55% |

## 2. 최종 후보 로직

### 2.1 매수 후보 필터

월말 기준으로 아래 조건을 모두 만족해야 합니다.

1. `20일 평균거래대금 >= 20억원`
2. `종가 >= 1,000원`
3. 강한 추세:
   - `종가 > 200일 이동평균`
   - `20일 이동평균 > 60일 이동평균`
   - `60일 이동평균 > 120일 이동평균`
4. `6개월 수익률 > +12%`
5. `최근 1개월 수익률 > -10%`
6. `종가 / 52주 고가 >= 0.72`

### 2.2 점수 산식

필터 통과 종목을 월별 횡단면 percentile rank로 점수화합니다.

```text
score =
  0.22 * r_ret_6m
  + 0.14 * r_ret_3m
  + 0.14 * r_near_high52
  + 0.13 * r_supply20_to_turnover
  + 0.10 * r_low_vol60
  + 0.10 * r_raw_material_cost_yoy
  + 0.09 * r_dart_material_yoy
  + 0.08 * r_annual_material_yoy
```

의미:

- 가격 추세 50%: 6개월/3개월 모멘텀, 52주 고점 근접도
- 수급 13%: 기관+외국인 20일 순매수 강도
- 안정성 10%: 60일 저변동성
- 매입재료비 증가 27%: 분기/연간 매입재료비 증가 신호

### 2.3 매매 규칙

1. 매월 마지막 거래일 이후 신호 계산
2. 다음 거래일 시가에 상위 5개 종목 균등매수
3. 기본 청산은 해당 월 마지막 거래일 종가
4. 보유 중 일별 종가가 진입가 대비 -20% 이하이면 그날 종가로 손절
5. 종목 부족 시 부족분은 현금 보유

## 3. 사용 데이터와 룩어헤드 처리

주요 원천:

- `price_history`: 가격, 거래량, 거래대금, 기관/외국인 수급
- `financial_data`: 분기 재무
- `cost_structure`, `dart_cost_quarterly`, `dart_material_purchase`: 매입재료비/원가 구조
- `order_backlog`: 수주잔고 후보 피처
- `short_rank_daily`: 대차/공매도 파생 피처 일부
- `research_outputs/market2x_signal_dataset.parquet`: 기존 월별 신호 데이터셋

시점 처리:

- 분기 재무/원가/수주 데이터는 기존 데이터셋 생성 로직에서 `quarter_signal_month(..., lag_months=2)` 방식으로 최소 2개월 지연 반영
- 연간 매입재료비는 결산월 이후 4개월 지연 반영
- 가격/수급 피처는 월말까지 확인된 값만 사용
- 체결은 신호월 종가가 아니라 다음 거래일 시가 기준으로 재계산

주의:

- 현재 연구는 `market2x_signal_dataset.parquet`를 사용합니다. 이 데이터셋 자체의 모든 저빈도 피처 지연 처리와 DART 공시일 정합성은 추가 검증 필요합니다.
- 2025-05 이후 완전 OOS 검증은 아직 수행하지 않았습니다.

## 4. 재현 방법

루트:

```bash
cd /Applications/stock_dashboard
```

전략 탐색:

```bash
python3 scripts/research_best_2021_2025_strategy.py
```

손절/트레일링 오버레이:

```bash
python3 scripts/research_strategy_risk_overlay.py
```

주요 산출물:

```text
research_outputs/best_2021_2025_strategy_summary.json
research_outputs/best_2021_2025_strategy_all_results.csv
research_outputs/best_2021_2025_strategy_top50.csv
research_outputs/best_2021_2025_risk_overlay_summary.json
research_outputs/best_2021_2025_risk_overlay_results.csv
research_outputs/best_2021_2025_risk_overlay_strong_trend__trend_material__plain__top5__stop20_monthly.csv
research_outputs/best_2021_2025_risk_overlay_strong_trend__trend_material__plain__top5__stop20_picks.csv
```

## 5. 이번에 추가/수정된 코드

### `scripts/research_best_2021_2025_strategy.py`

역할:

- 2021-01~2025-03 신호월, 2025-04 청산까지 백테스트
- 1억원 예산, 월별 균등 배분
- 다음 거래일 시가 진입, 해당 월말 종가 청산
- 3,480개 후보 규칙 탐색
- 시장 레짐 필터 후보 포함

핵심 구현:

- `attach_next_open_returns`: 다음 거래일 시가 매수 수익률 계산
- `attach_market_regime`: KOSPI 월간 레짐 피처 추가
- `candidate_rules`: 추세/매입재료비/수급/저변동성/수주/시장필터 조합 생성
- `backtest_rule`: 월별 포트폴리오 백테스트

### `scripts/research_strategy_risk_overlay.py`

역할:

- 상위 후보 전략에 고정 손절/트레일링 스탑 적용
- `stop8`, `stop10`, `stop12`, `stop15`, `stop20`, `trail10`, `trail12`, `trail15`, `trail20` 비교
- 최종 최선: `strong_trend__trend_material__plain__top5__stop20`

## 6. 검증 결과 디테일

연도별 성과:

| 연도 | 수익률 | 월 승률 | 월 최저수익률 |
|---|---:|---:|---:|
| 2021 | -3.19% | 50.0% | -9.88% |
| 2022 | +63.78% | 83.3% | -14.18% |
| 2023 | -11.28% | 41.7% | -11.85% |
| 2024 | +54.02% | 58.3% | -8.06% |
| 2025.01~03 | +16.24% | 100.0% | +0.71% |

취약 구간:

- 2021-09~2021-10
- 2022-05
- 2023-03
- 2023-08~2023-10

해석:

- 추세형 로직이라 시장/테마 꺾임 구간에서 손실이 발생합니다.
- `stop20`은 큰 승자를 과도하게 자르지 않으면서 일부 급락을 줄였습니다.
- 더 촘촘한 손절(`stop8~15`)이나 트레일링은 대체로 수익을 깎았습니다.

## 7. Claude에게 요청하는 다음 작업

우선순위 P0:

1. **2025-05~현재 완전 OOS 검증**
   - 현재 모델 개발/선택에 사용되지 않은 기간입니다.
   - 같은 체결 가정으로 월별 성과와 매수 종목을 산출하세요.

2. **룩어헤드 재검증**
   - `market2x_signal_dataset.parquet`의 저빈도 피처가 실제 공시/확인 가능일 이후에만 붙었는지 확인하세요.
   - 특히 `dart_cost_quarterly`, `dart_material_purchase`, `cost_structure`, `order_backlog`의 `source_rcept_dt`, `created_at`, `collected_at` 사용 가능성을 점검하세요.

3. **현재 사이트 전략 엔진에 연결 가능한 형태로 룰 함수화**
   - 후보 함수명 제안: `strategy_strong_trend_material_stop20`
   - 입력: 월말 기준 종목별 피처 테이블
   - 출력: `stock_code`, `score`, `rank`, `position_weight`, `entry_rule`, `exit_rule`, `risk_flags`

우선순위 P1:

4. **슬리피지 민감도**
   - 왕복 비용 0.35%, 0.7%, 1.0%, 1.5%별 성과 비교
   - 평균거래대금 대비 주문금액 영향 점검

5. **포지션 사이징 개선**
   - 현재는 5종목 균등 배분
   - 점수 가중, 변동성 역가중, 최대 25% 제한 등을 비교

6. **시장 레짐 재검토**
   - 이번 레짐 필터는 최고수익을 넘지 못했습니다.
   - 다만 낙폭 개선 후보가 있으므로 2025 OOS에서 다시 비교하세요.

우선순위 P2:

7. **기존 사이트 UI 반영**
   - 전략 설명 테이블에 필터/점수/손절 규칙 추가
   - 전략 성과 매트릭스에 위 성과 추가
   - 단, 사용자가 이전에 해당 작업은 Claude 진행 중이라고 했으므로 중복 작업 여부 확인 필요

## 8. 실전 적용 전 경고

이 로직은 현재 백테스트상 시장수익률을 크게 초과하지만 아직 실전 승인 상태가 아닙니다.

주요 리스크:

- 매입재료비 데이터 커버리지와 파싱 품질이 종목별로 다름
- 2023년 같은 역추세/테마 붕괴 구간에서 손실 가능
- 거래대금 20억원 필터가 있어도 일부 종목은 실제 1억원 운용 시 체결 충격 가능
- 월별 리밸런싱이라 급락장 초입 대응이 늦을 수 있음
- 후보 규칙을 3,480개 탐색했으므로 과최적화 가능성이 존재

Claude는 이 문서를 구현 지시서가 아니라 "현재까지 가장 강한 연구 후보"로 보고, P0 검증을 완료한 뒤 사이트/실전 로직 반영 여부를 판단해야 합니다.
