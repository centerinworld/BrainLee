# Codex handoff: forward winner-signal logic validation

작성일: 2026-06-23

## 목적

기존 구축 로직은 의사결정에 사용하지 않고, "나중에 수익이 좋았던 종목군에서 관찰되는 사전 특징"을 기반으로 새 종목 발굴 로직을 만들었다. 단, 종목 선정 필터와 스코어에는 미래 수익률 컬럼을 쓰지 않았다.

이번 실행은 실매매 승인용이 아니라 Claude 검증용 후보 산출이다.

## 재현 명령

```bash
cd /Applications/stock_dashboard
python3 scripts/research_forward_winner_signal_logic_20260623.py
```

## 입력과 실행 가정

- 입력 데이터: `/Applications/stock_dashboard/research_outputs/market2x_signal_dataset.parquet`
- 가격 DB: `/Applications/stock_dashboard/stock.db`
- 대상: KOSPI/KOSDAQ 6자리 종목코드
- 신호월: 2021-01~2026-05
- 체결 가정: 신호월 다음달 첫 거래일 시가 매수, 해당월 말 종가 청산
- 월별 편입: 각 로직 상위 5개 동일가중
- 비용: 월 리밸런싱당 0.70% 차감
- 시장 필터: 로컬 `^KS11` 월봉 기준 상승/비하락 regime만 사용
- 벤치마크: 같은 월 매수 가능 유동성 유니버스 동일가중 평균
- KOSPI 지수 수익률은 참고만 한다. 로컬 `^KS11` 2025-06~2026-05 구간이 +167.08%로 계산되어 데이터 품질 검증 전에는 승인 기준으로 쓰면 안 된다.

## 승자군 사전 특징 메모

`target_market2x_6m` 기준 승자군과 비승자군의 중앙값 비교에서 다음 특징이 상대적으로 높게 나타났다.

| feature | winner median | nonwinner median | coverage |
|---|---:|---:|---:|
| `ret_3m` | 0.0469 | 0.0301 | 98.6% |
| `near_high52` | 0.7712 | 0.7473 | 95.7% |
| `above_low52` | 0.4809 | 0.4012 | 95.7% |
| `fin_rev_yoy` | 0.0244 | 0.0052 | 86.8% |
| `fin_op_yoy` | -0.0696 | -0.1261 | 83.4% |
| `cf_ocf_margin` | 0.0685 | 0.0609 | 45.8% |
| `raw_material_cost_yoy` | 0.0318 | 0.0095 | 10.5% |

해석: 가격은 이미 완전한 고점 추격보다는 회복/초기 추세 쪽이 유리했고, 매출 성장, 영업이익 감소 폭 축소, 현금흐름 마진, 원재료 투입 증가 같은 실물 신호가 일부 동반됐다. 다만 수출/수주/원재료 계열은 coverage가 낮아 과신하면 안 된다.

## 테스트한 신규 로직

### 1. `forward_growth_export_quality_top5`

아이디어: 매출/영업이익 성장과 수출 성장 확인 후, 아직 52주 고점 추격이 아닌 초기 추세만 선별한다.

필터:

- `avg_turnover20 >= 2e9`, `close >= 1000`
- `close`가 `ma60`의 0.97~1.22 범위
- `ma20 >= ma60 * 0.96`
- `ret_6m` -10%~+80%
- `near_high52` 0.45~0.96
- 월별 횡단면 `fin_rev_yoy` 상위 30%
- 월별 횡단면 `fin_op_yoy` 상위 30%
- `export_yoy > 0`

스코어: `fin_rev_yoy`, `fin_op_yoy`, `export_yoy`, `cf_ocf_margin`, `supply20_to_turnover`, 저변동성, `ret_3m` 가점, `near_high52` 감점.

### 2. `forward_supply_demand_base_top5`

아이디어: 수급 누적/저변동 베이스 위에 수주잔고, 원재료 투입, 수출 중 하나 이상의 수요 신호가 붙은 종목을 선별한다.

필터:

- 유동성/초기 추세/비과열 조건 동일
- `above_low52` 0.15~2.50
- 월별 횡단면 `supply60_to_turnover` 상위 30%
- 월별 횡단면 저변동성 상위 40%
- `backlog_yoy`, `raw_material_cost_yoy`, `annual_material_yoy` 상위 30% 또는 `export_yoy > 10%`

스코어: `supply60_to_turnover`, 저변동성, `backlog_yoy`, `raw_material_cost_yoy`, `annual_material_yoy`, `export_yoy`, `fin_rev_yoy` 가점, `ret_6m`/`near_high52` 감점.

### 3. `forward_turnaround_cashflow_top5`

아이디어: 매출 가속/영업이익 턴어라운드와 현금흐름 품질이 같이 개선되는 초기 회복형 종목을 선별한다.

필터:

- 유동성/초기 추세/비과열/저점 회복 조건 동일
- `fin_rev_accel > 0`
- `fin_op_turnaround > 0`
- 월별 횡단면 `cf_ocf_margin` 상위 40%

스코어: `fin_rev_accel`, `fin_op_yoy`, `cf_ocf_margin`, `supply20_to_turnover`, 저변동성, `above_low52`, `ret_3m` 가점, `fin_debt_ratio` 감점.

## 백테스트 결과

| logic | split | return | liquid universe | alpha | MDD | trades |
|---|---|---:|---:|---:|---:|---:|
| growth/export/quality | Train 2021-01~2023-12 | -25.14% | -20.57% | -4.57%p | -30.81% | 81 |
| growth/export/quality | Valid 2024-01~2025-05 | +7.92% | +2.70% | +5.22%p | -26.44% | 35 |
| growth/export/quality | OOS 2025-06~2026-05 | +17.46% | +13.21% | +4.25%p | -25.31% | 46 |
| growth/export/quality | All 2021-01~2026-05 | -5.11% | -7.65% | +2.54%p | -43.50% | 162 |
| supply/demand/base | Train | -2.11% | -20.57% | +18.46%p | -9.93% | 30 |
| supply/demand/base | Valid | -1.95% | +2.70% | -4.65%p | -20.42% | 40 |
| supply/demand/base | OOS | +11.20% | +13.21% | -2.01%p | -18.44% | 60 |
| supply/demand/base | All | +6.73% | -7.65% | +14.38%p | -28.32% | 130 |
| turnaround/cashflow | Train | +63.95% | -20.57% | +84.52%p | -17.97% | 39 |
| turnaround/cashflow | Valid | -6.73% | +2.70% | -9.43%p | -21.28% | 7 |
| turnaround/cashflow | OOS | +0.91% | +13.21% | -12.30%p | -8.72% | 20 |
| turnaround/cashflow | All | +54.32% | -7.65% | +61.97%p | -22.68% | 66 |


## 2026-06-23 추가 개선 결과

사용자 요청에 따라 기존 후보를 더 개선했고, 신규 2개 로직을 정식 백테스트에 추가했다. 두 로직 모두 기존 후보 대비 OOS 수익률이 크게 개선됐다.

| logic | split | return | liquid universe | alpha | MDD | trades |
|---|---|---:|---:|---:|---:|---:|
| improved growth/export/momentum | Train 2021-01~2023-12 | -14.07% | -20.57% | +6.50%p | -17.33% | 28 |
| improved growth/export/momentum | Valid 2024-01~2025-05 | +74.78% | +2.70% | +72.08%p | -19.24% | 24 |
| improved growth/export/momentum | OOS 2025-06~2026-05 | +91.76% | +13.21% | +78.55%p | -18.80% | 42 |
| improved growth/export/momentum | All 2021-01~2026-05 | +188.02% | -7.65% | +195.67%p | -19.24% | 94 |
| improved quality/value/momentum | Train | -4.36% | -20.57% | +16.21%p | -11.37% | 46 |
| improved quality/value/momentum | Valid | -14.03% | +2.70% | -16.73%p | -22.92% | 21 |
| improved quality/value/momentum | OOS | +48.44% | +13.21% | +35.23%p | -3.35% | 59 |
| improved quality/value/momentum | All | +22.05% | -7.65% | +29.70%p | -29.50% | 126 |

### 개선 로직 1: `improved_growth_export_momentum_top5`

필터:

- 유동성: `avg_turnover20 >= 2e9`, `close >= 1000`
- 초기 추세: `close`가 `ma60`의 0.97~1.22 범위, `ma20 >= ma60 * 0.96`
- 과열 제한: `ret_6m` -10%~+80%, `near_high52` 0.40~1.02
- 월별 횡단면 `fin_rev_yoy` 상위 30%
- 월별 횡단면 `fin_op_yoy` 상위 40%
- 월별 횡단면 `export_yoy` 상위 40%
- 월별 횡단면 `supply20_to_turnover` 상위 40%

스코어:

- `fin_rev_yoy` 0.20
- `fin_op_yoy` 0.18
- `fin_op_margin` 0.12
- `cf_ocf_margin` 0.12
- 저변동성 0.14
- `supply20_to_turnover` 0.10
- `ret_3m` 0.06
- `near_high52` -0.12
- `ret_6m` -0.08

주의: `ret_col='ret_m1'`로 손절 없는 월말청산이다. 수익은 가장 좋지만, 실매매 전에는 -10% 손절/추적손절 버전으로 수익 훼손 정도를 다시 확인해야 한다.

### 개선 로직 2: `improved_quality_value_momentum_top8`

필터:

- 유동성: `avg_turnover20 >= 2e9`, `close >= 1000`
- 살아 있는 추세: `ma20 > ma60`, `close > ma120 * 0.96`
- 과열 제한: `ret_6m` -10%~+80%, `near_high52` 0.45~0.96
- 월별 횡단면 `fin_rev_yoy` 상위 40%
- 월별 횡단면 `fin_op_yoy` 상위 40%
- 월별 횡단면 `cf_ocf_margin` 상위 40%
- 월별 횡단면 `supply60_to_turnover` 상위 40%

스코어:

- `fin_rev_yoy` 0.16
- `fin_op_yoy` 0.14
- `cf_ocf_margin` 0.14
- 저변동성 0.14
- `supply60_to_turnover` 0.12
- `export_yoy` 0.10
- `above_low52` 0.08
- `near_high52` -0.10
- `ret_6m` -0.08

주의: OOS 수익과 MDD는 좋지만 validation 구간이 -14.03%로 약하다. 단독 승인보다 1번 로직의 보조/분산 로직으로 검증해야 한다.

## Codex 판단

개선 후에도 실매매 자동 승인까지는 보류하되, `improved_growth_export_momentum_top5`는 최우선 검증 후보로 격상한다.

- `forward_growth_export_quality_top5`: OOS는 벤치마크 대비 +4.25%p지만 전체 기간 절대수익 -5.11%, MDD -43.50%라 방어력이 부족하다.
- `forward_supply_demand_base_top5`: 전체 기간 알파는 +14.38%p지만 OOS에서 벤치마크에 -2.01%p 뒤진다. 보조 시그널 후보로만 유지.
- `forward_turnaround_cashflow_top5`: 전체 기간 수익은 가장 좋지만 validation/OOS가 약하다. 2021~2023 특정 국면 과적합 가능성이 크다.

따라서 다음 단계는 “후보 승인”이 아니라 “데이터 누수와 피처 lag를 검증한 뒤, 규칙을 더 보수적으로 재설계”하는 것이다.

## Claude 검증 요청

1. 미래 데이터 누수 검증
   - `market2x_signal_dataset.parquet`의 `fin_*`, `cf_*`, `export_*`, `backlog_*`, `raw_material_*` 값이 신호월 당시 실제 관측 가능했는지 확인.
   - 특히 재무/현금흐름은 공시 지연 lag, 수출/원재료/수주 데이터는 발표 지연 lag가 반영됐는지 검증.

2. 선정 코드 검증
   - `/Applications/stock_dashboard/scripts/research_forward_winner_signal_logic_20260623.py`
   - `target_market2x_*`, `target_bigwinner_*`, `fwd_*` 컬럼이 pick mask/score에 들어가지 않았는지 확인.

3. KOSPI `^KS11` 데이터 품질 확인
   - 이번 결과에서 `^KS11` OOS 수익률이 +167.08%로 계산된다.
   - 로컬 지수 데이터가 실제 지수인지, 스케일/수정주가/심볼 매핑 오류가 있는지 확인.

4. 후보 집중도 확인
   - 각 picks 파일에서 특정 1~2개 종목, 특정 섹터, 특정 월에 수익이 과도하게 집중됐는지 분해.
   - 월별 pick 수가 적은 로직은 현금 비중/미체결 가능성을 별도로 반영.

5. 재설계 제안
   - growth/export는 MDD 축소 조건이 필요하다.
   - supply/demand는 OOS 알파 회복 조건이 필요하다.
   - turnaround/cashflow는 validation/OOS 부진 원인을 먼저 분해해야 한다.

## 산출 파일

- `/Applications/stock_dashboard/scripts/research_forward_winner_signal_logic_20260623.py`
- `/Applications/stock_dashboard/research_outputs/forward_winner_signal_logic_20260623_result.json`
- `/Applications/stock_dashboard/research_outputs/forward_winner_signal_logic_20260623_summary.csv`
- `/Applications/stock_dashboard/research_outputs/forward_winner_signal_logic_20260623_feature_notes.csv`
- `/Applications/stock_dashboard/research_outputs/forward_growth_export_quality_top5_20260623_monthly.csv`
- `/Applications/stock_dashboard/research_outputs/forward_growth_export_quality_top5_20260623_picks.csv`
- `/Applications/stock_dashboard/research_outputs/forward_supply_demand_base_top5_20260623_monthly.csv`
- `/Applications/stock_dashboard/research_outputs/forward_supply_demand_base_top5_20260623_picks.csv`
- `/Applications/stock_dashboard/research_outputs/forward_turnaround_cashflow_top5_20260623_monthly.csv`
- `/Applications/stock_dashboard/research_outputs/forward_turnaround_cashflow_top5_20260623_picks.csv`
