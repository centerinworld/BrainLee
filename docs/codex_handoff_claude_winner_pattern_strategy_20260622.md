# Codex handoff: winner-pattern strategy 재검토

작성일: 2026-06-22

## 결론

기존 `strong_trend_material_stop20`은 실매매 불가로 폐기하고, 많이 오른 종목의 사전 특징을 먼저 학습한 뒤 조합하는 방식으로 새 후보를 만들었다.

새 후보는 “매수 가능 유니버스 동일가중 시장평균” 기준으로는 통과한다. 다만 `^KS11` 지수 데이터가 2025-06~2026-05에 +196.73%로 계산되는 이상치가 있어, 지수 벤치마크는 데이터 품질 검증 전까지 실매매 승인 기준으로 쓰면 안 된다.

## 새 후보 로직

전략명:

`winner_pattern_turnaround_flow_fin_export`

탐색 결과 상위 rule:

`turnaround_flow|early_trend & fin_rev_q70 & fin_op_q70 & export_q70|hit0.15|lift3.10`

매수 후보 필터:

- `early_trend`: 현재가가 MA60의 0.97~1.20 범위이고 MA20이 MA60의 0.96 이상
- `fin_rev_q70`: 같은 월 횡단면 기준 매출 YoY 상위 30%
- `fin_op_q70`: 같은 월 횡단면 기준 영업이익 YoY 상위 30%
- `export_q70`: 같은 월 횡단면 기준 수출 YoY 상위 30%
- KOSPI regime filter: `regime_bull == 1`, `regime_bear == 0`

월별 선정:

- 매월 신호월 기준 후보 산출
- 다음달 첫 거래일 시가 매수 가정
- 월별 최대 5개 종목
- 월 내 동일가중
- 거래비용 0.70% 차감

매도:

- 기본: 다음달 월말 종가 청산
- 리스크 버전: 월중 종가 기준 -10% 손절 또는 고점 대비 -12% 추적손절
- 현재 최상위 결과는 `ret_m1_stop12_trail` 기준

스코어:

- `fin_rev_accel` 0.16
- `supply20_to_turnover` 0.16
- `short_cover_3m` 0.10
- `low_vol60` 0.12
- `ret_3m` 0.12
- `above_low52` 0.10
- `near_high52` -0.10
- `fin_debt_ratio` -0.08

## 백테스트 결과

검증 기간:

- Train: 2021-01~2023-12
- Validation: 2024-01~2025-05
- OOS: 2025-06~2026-05
- All: 2021-01~2026-05

시장평균 기준:

| 구간 | 전략수익률 | 매수가능 유니버스 평균 | 알파 | MDD | 거래수 | 월 승률 | Sharpe |
|---|---:|---:|---:|---:|---:|---:|---:|
| Train | +91.32% | -28.18% | +119.50%p | -12.81% | 75 | 68.4% | 1.03 |
| Validation | +13.85% | +1.01% | +12.84%p | -26.64% | 26 | 50.0% | 0.43 |
| OOS | +101.30% | +19.62% | +81.69%p | -8.08% | 42 | 75.0% | 2.47 |
| All | +338.50% | -13.23% | +351.72%p | -26.64% | 143 | 66.7% | 1.13 |

참고 지수 기준:

- OOS `^KS11` 벤치마크: +196.73%
- All `^KS11` 벤치마크: +206.25%
- 위 값은 현재 로컬 DB상으로 계산된 값이며, 한국 시장 일반 지수 움직임으로 보기 어렵다. `price_history`의 `^KS11` 2026년 구간을 데이터 품질 이슈로 따로 검증해야 한다.

## 기존 실패 로직과의 차이

폐기된 로직:

`strong_trend_material_stop20`

문제:

- 이미 크게 오른 종목을 MA 정배열과 6개월 모멘텀으로 따라가는 구조
- 2025-04~2026-04 강세 13개월 구간에서는 +174.4%처럼 보였으나, 2021-01~2026-05 확장 검증에서는 OOS -10.6%, MDD -51.2%
- “52주 고점 근접 + 강한 추세”가 반전 시 큰 손실로 연결됨

새 후보의 차이:

- 가격 추세 단독이 아니라 `매출 성장 + 영업이익 성장 + 수출 성장`이 동시에 확인된 초기 추세만 선택
- `near_high52`를 가점이 아니라 감점으로 반영해 고점 추격을 줄임
- 수급/공매도/변동성은 순위화된 보조 스코어로만 사용

## Claude 검증 요청

1. `^KS11` 데이터 품질 확인
   - `price_history`에서 `^KS11` 2025-06~2026-06 구간이 비정상적으로 급등한다.
   - 지수 데이터가 실제 지수인지, 다른 값이 섞였는지, split/scale 오류가 있는지 확인.

2. 저빈도 피처 확인 가능일 검증
   - `fin_rev_yoy`, `fin_op_yoy`, `export_yoy`가 신호월 당시 실제로 확인 가능한 데이터만 붙었는지 확인.
   - 특히 재무 데이터는 공시 지연, 수출 데이터는 월별 발표 지연을 반영해야 한다.

3. 후보 종목 리스트 재현
   - 실제 월별 picks는 `winner_pattern_strategy_best_picks.csv`에 저장했다.
   - 종목별 편중이 있는지, 특정 1~2개 종목 수익에 의존하는지 확인.

4. 실매매 승인 조건
   - 현 상태는 `candidate_pass`이지 최종 실매매 승인 아님.
   - 승인 전 조건:
     - 지수/가격 데이터 이상치 해결
     - 피처 lag 검증
     - 월별 picks 저장 및 종목별 기여도 분해
     - 2021~2026 외 추가 walk-forward 검증

## 관련 파일

- `/Applications/stock_dashboard/scripts/research_winner_pattern_strategy.py`
- `/Applications/stock_dashboard/research_outputs/winner_pattern_strategy_result.json`
- `/Applications/stock_dashboard/research_outputs/winner_pattern_strategy_results.csv`
- `/Applications/stock_dashboard/research_outputs/winner_pattern_strategy_best_picks.csv`
- `/Applications/stock_dashboard/docs/codex_review_strong_trend_material_logic_20260622.md`
