# Claude Handoff: 2025-05~2026-05 대세상승장 승자 신호 탐색

작성일: 2026-06-24

## 목적

사용자 지시에 따라 사전 로직을 정하지 않고, 2025-05~2026-05 대세상승장에서 실제 많이 상승한 종목들의 공통 특징을 먼저 찾았다. 이후 같은 신호를 가진 실패 종목과의 차이를 비교하고, 고정 익절이 아닌 추세 훼손형 매도 후보를 비교했다.

## 사용 데이터

- 가격/수급: `price_history`
- 통합 월별 피처: `research_outputs/market2x_signal_dataset.parquet`
- 포함 피처: 수출입, NPS 인력, 수주/수주잔고, 공시/계약, 내부자, 공매도/대차, 신용, 재무, 현금흐름, 원가/원재료, 추세선/거래대금
- 신호 snapshot: 2025-04-30 이전 최신 값
- 수익률 구간: 2025-05-02 ~ 2026-05-29
- 유니버스: 유동성 필터 후 보통주 1,701개

주의: 2026-06-24에 `price_history`의 KRX 지수 행(`^KS11`, `^KQ11`, `^KS200`, `^KQ150`)을 KRX 공식 API 값으로 정정했다. 2026-06-24 지수 행은 아직 KRX 공식 일봉이 없어 삭제했고, 최신 공식 지수일은 2026-06-23이다. 이번 비교 기준은 동일 유니버스 평균/중앙값이며, KOSPI 벤치마크는 정정 후 별도 재산출 대상이다.

## 시장 기준

- 유니버스 평균 수익률: +47.7%
- 유니버스 중앙값 수익률: +4.5%
- 상위 10% 기준: +170.6%
- 상위 5% 기준: +288.4%
- 손실 종목 비율: 45.5%

## 상승 종목 공통 특징

상위 피처는 아래 순서로 강했다.

1. `export_value`: 수출액 상위 20%의 승자 확률 28.8%, base 10.1% 대비 +186.9%
2. `import_value`: 수입액 상위 20%의 승자 확률 26.9%, base 대비 +167.8%
3. `export_yoy`: 수출 증가율 상위 20%의 승자 확률 24.1%
4. `borrow_bal_amt`, `borrow_bal_qty`: 대차잔고 규모가 큰 종목의 승자 확률 상승
5. `high52`, `ma120`, `ma20`, `ma60`, `ma200`: 이미 가격 기반이 큰 종목, 즉 완전 바닥주가 아니라 대형 추세 후보
6. `turnover`, `avg_turnover20`: 거래대금 기반

해석: 2025-05~2026-05의 승자는 단순 저평가/과매도가 아니라, 수출입 밸류체인 규모가 있고 시장 관심이 이미 붙은 IT/반도체/전력기기 중심 종목이었다.

## 같은 신호 실패 종목과의 차이

대표 신호 `import_value 상위 + borrow_bal_qty 상위 + high52 상위` 내부에서 성공/실패를 나눈 추가 차이는 다음이다.

- `import_yoy`: 성공 중앙값 +10.95%, 실패 중앙값 -6.92%
- `fin_roe`: 성공 5.07, 실패 0
- `fin_net_margin`: 성공 15.5%, 실패 1.2%
- `fin_net_yoy`: 성공 +22.5%, 실패 -61.8%
- `raw_material_ratio`: 성공 57.1%, 실패 19.8%

해석: 같은 수출입/대차/가격 기반 신호라도, 실제 상승한 종목은 수입 증가가 둔화되지 않고 이익의 질이 살아 있었다. 실패 종목은 매출/수입 노출만 있고 ROE/순마진/순이익 증가가 약했다.

## 후보 신호

### A: Import + Borrow + High52 + Quality

월별 cross-section 조건:

- `import_value_rank >= 0.75`
- `borrow_bal_qty_rank >= 0.75`
- `high52_rank >= 0.65`
- `import_yoy >= 0`
- `fin_roe > 0`

스코어:

- `import_value_rank` 0.25
- `borrow_bal_qty_rank` 0.20
- `high52_rank` 0.15
- `import_yoy_rank` 0.15
- `fin_roe_rank` 0.15
- `avg_turnover20_rank` 0.10

### D: Import YoY + Quality + Liquidity

월별 cross-section 조건:

- `import_value_rank >= 0.70`
- `import_yoy_rank >= 0.55`
- `fin_roe > 0`
- `avg_turnover20_rank >= 0.65`

스코어:

- `import_value_rank` 0.25
- `import_yoy_rank` 0.20
- `fin_roe_rank` 0.20
- `fin_net_margin_rank` 0.15
- `avg_turnover20_rank` 0.20

## 동적 검증 결과

기간: 2025-05-02 ~ 2026-05-29, 최대 10종목, 월별 신호, 동일 비중.

| 전략 | 매도 | 총수익 | CAGR | 시장평균 대비 | MDD | 거래 | 승률 |
|---|---|---:|---:|---:|---:|---:|---:|
| A | 보유 지속 | +292.7% | +257.7% | +245.0%p | -88.6% | 10 | 80.0% |
| D | 보유 지속 | +261.9% | +231.5% | +214.2%p | -91.0% | 10 | 80.0% |
| A | 고점 대비 -15% | +174.2% | +156.0% | +126.5%p | -54.8% | 37 | 59.5% |
| A | MA10 이탈 + 고점 대비 -10% | +148.1% | +133.2% | +100.4%p | -60.2% | 51 | 64.7% |
| A | MA20 이탈 + MA20 5일 기울기 하락 | +139.4% | +125.6% | +91.7%p | -70.3% | 50 | 64.0% |

기준 미달 전략은 기록하지 않았다. 위 후보들은 모두 유니버스 평균 +47.7%를 초과했다.

## 매도 분석

상위 상승주 120개에서 고점 이후 처음 발생하는 신호 기준:

- 고점 대비 -10%: 고점 후 중앙 4일, 고점 수익의 84.3% 보존
- MA10 이탈: 고점 후 중앙 6일, 고점 수익의 82.7% 보존
- 고점 대비 -15%: 고점 후 중앙 6일, 고점 수익의 78.5% 보존
- MA20 이탈: 고점 후 중앙 9일, 고점 수익의 75.2% 보존
- MA20 이탈 + MA20 기울기 하락: 고점 후 중앙 21일, 고점 수익의 70.0% 보존

해석: 사용자 경험처럼 추세가 꺾이는 지점이 매도 시점이라는 가설은 데이터와 대체로 맞다. 다만 MA20 기울기까지 기다리면 너무 늦고, 단순 MA10/고점 대비 -10~-15% 훼손이 고점 수익 보존율은 더 높다.

## 산출물

- `/Applications/stock_dashboard/scripts/research_bull_winner_signal_discovery_20260624.py`
- `/Applications/stock_dashboard/scripts/backtest_bull_discovered_signals_20260624.py`
- `/Applications/stock_dashboard/research_outputs/bull_winner_discovery_20260624/report.md`
- `/Applications/stock_dashboard/research_outputs/bull_winner_discovery_20260624/summary.json`
- `/Applications/stock_dashboard/research_outputs/bull_winner_discovery_20260624/winner_feature_rank.csv`
- `/Applications/stock_dashboard/research_outputs/bull_winner_discovery_20260624/same_signal_failure_contrast.csv`
- `/Applications/stock_dashboard/research_outputs/bull_winner_discovery_20260624/sell_signal_peak_capture.csv`
- `/Applications/stock_dashboard/research_outputs/bull_winner_discovery_20260624/dynamic_candidate_backtests.csv`

## Claude 검증 요청

1. `market2x_signal_dataset.parquet`의 각 피처가 2025-04-30 당시 실제 관측 가능했는지 lag 검증.
2. `volume_y`처럼 종료일 데이터가 피처에 섞이는 누수를 차단했는지 재확인.
3. A/D 후보를 2021~2026 rolling 월별로 재검증. 특히 2025-05~2026-05에 과최적화됐는지 확인.
4. MDD 계산과 가격 조정 데이터 문제 확인. 보유 지속의 MDD가 매우 크므로 split/adjusted price와 실제 고점 대비 하락을 분리 검증.
5. 매도는 고정 익절 금지. `고점 대비 훼손 + 단기 MA 이탈`과 `MA20 slope`를 더 정교하게 비교.
6. KRX 지수 정정 후 KOSPI 대비 alpha를 재산출.
