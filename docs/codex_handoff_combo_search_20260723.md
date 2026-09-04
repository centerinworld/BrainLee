# Codex Handoff — 병합계좌 최고 조합 탐색 2026-07-23

## 목적

사용자가 클로드의 병합계좌 조합 탐색 결과(+510.1%)처럼 Codex도 더 좋은 매수/매도 전략 조합을 찾아보라고 요청했다. Codex는 기존 `merged_simulator.py`를 그대로 사용해 1억원 단일 현금계좌 기준으로 추가 탐색했다.

## 사용 엔진

- 엔진: `merged_simulator.simulate_merged_account`
- 초기자본: 100,000,000원
- 체결 순서: 같은 날 매도 먼저, 이후 우선순위 높은 매수부터 체결
- 티켓: 10,000,000원
- 동적 티켓 확장: 켬
- 수수료/세금/슬리피지: 기존 `MergeConfig` 기본값
- 기간: 2020-03-01 ~ 2026-03-31
- 주의: 2026-07까지 이어지는 source stream은 2026-03-31까지만 잘라 사용했다.

## 생성 산출물

- 탐색 스크립트: `scratch/codex_combo_search_20260723.py`
- 1차 탐색 CSV: `research_outputs/codex_combo_search_20260723.csv`
- 정밀 탐색 CSV: `research_outputs/codex_combo_focus_search_20260723.csv`

## 핵심 결과

| 등급 | 조합 | 수익률 | 거래 | 승률 | 판정 |
|---|---|---:|---:|---:|---|
| 연구용 최고 | EARN + MOON + SECTOR + V2 + V4 + RECOVERY + V10 + V5 | +643.05% | 1,045 | 40.29% | V5가 run_spec 없는 legacy stream이라 정식 채택 금지 |
| 명세 존재 최고 후보 | EARN + MOON + SECTOR + V2 + V4 + RECOVERY + V10 | +605.05% | 995 | 39.60% | v2/recovery/v10이 point_in_time_approx라 추가 검증 필요 |
| 보수적 개선 후보 | EARN + MOON + SECTOR + V2 + V4 + RECOVERY | +578.35% | 839 | 40.88% | recovery 추가만으로 +39.2%p 개선 |
| 현재 정식등록 최고 | EARN + MOON + SECTOR + V2 + V4 | +539.18% | 793 | 40.98% | DB 등록됨, run_id `cmb_4021ffe7f9a5` |
| 엄격 위주 축소 | EARN + MOON + SECTOR + V4 | +455.76% | 626 | 38.50% | v2/recovery/v10 제거 시 성과 하락 |

## 최고 후보 우선순위

### 연구용 최고 +643.05%

```json
{
  "earn": 4.0,
  "moon30": 3.0,
  "sector": 1.0,
  "v2": 0.8,
  "v4": 0.6,
  "recovery": 0.4,
  "v10": 0.3,
  "v5": 0.1
}
```

### 정식 후보로 볼 수 있는 최고 +605.05%

```json
{
  "earn": 4.0,
  "moon30": 3.0,
  "sector": 1.0,
  "v2": 0.8,
  "v4": 0.6,
  "recovery": 0.4,
  "v10": 0.3
}
```

## 원천 run 검증 상태

| 소스 | run name | 상태 | 메모 |
|---|---|---|---|
| EARN | `MERGE_SRC3_earnings_conviction_20260723` | `execution_strict` | next_open, 비용/현금원장 통과 |
| MOON | `MERGE_SRC3_moonshot_turnaround_20260723` | `execution_strict` | next_open, 비용/현금원장 통과 |
| SECTOR | `MERGE_SRC2_sector_focus_20260718` | `execution_strict` | current universe 한계는 있음 |
| V4 | `MERGE_SRC2_v4_20260718` | `execution_strict` | current universe 한계는 있음 |
| V2 | `MERGE_SRC2_v2_20260718` | `point_in_time_approx` | 주식수/유니버스 근사 |
| RECOVERY | `RECOVERY_CONTINUOUS_FORCOMBO_20260722` | `point_in_time_approx` | 주식수/유니버스 근사 |
| V10 | `GATE_ANALYSIS_v10_20260718` | `point_in_time_approx` | 주식수/유니버스 근사 |
| V5 | `STRICT v5` | legacy/no run_spec | 연구용으로만 사용, 정식 등록 금지 |

## 해석

이번 탐색에서 가장 중요한 발견은 `V-RECOVERY`와 `V10`이 기존 핵심 조합의 빈 구간을 잘 메운다는 점이다.

- `V-RECOVERY` 단독 수익률은 아주 높지 않지만, 기존 조합에 낮은 우선순위로 추가하면 +539.18% → +578.35%로 상승했다.
- `V10`을 더 낮은 보조로 붙이면 +605.05%까지 상승했다.
- `V5`는 추가 시 +643.05%까지 올라가지만, 원천 stream이 run_spec 없는 legacy라 정식 조합으로 채택하면 안 된다.

메커니즘 가설:

1. EARN/MOON은 저빈도 고가치 신호라 최우선권을 줘야 한다.
2. SECTOR/V2/V4는 남는 자본을 채우는 중간 빈도 보조 역할이 좋다.
3. RECOVERY/V10은 시장 국면이 바뀌는 구간에서 기존 조합의 공백을 메운다.
4. 너무 고빈도인 전략을 높은 우선순위에 놓으면 EARN/MOON 신호를 밀어내 수익률이 악화될 가능성이 크다.

## 채택 판단

현재 즉시 정식 등록 권장:

- 없음. `+605.05%` 후보는 좋아 보이나 `point_in_time_approx` 구성요소가 3개라 “최종 채택” 전 추가 검증이 필요하다.

후보 등록/프론트 표시 권장:

- `+605.05%` 조합을 `candidate/research` 상태로 표시
- 화면 문구: “PIT 근사 포함, 정식 실전 조합 아님”

정식 채택 전 필수 확인:

1. `V2`, `RECOVERY`, `V10`의 source run을 `point_in_time_verified` 또는 최소 동일 소스 스냅샷 기반 suite로 재생성.
2. `V5`는 run_spec/아티팩트가 없어 +643% 결과를 절대 정식 채택하지 말 것.
3. 후보 조합을 6기간 표준 매트릭스로 다시 분해해서 특정 구간 한 방인지 확인.
4. 월별 MDD, 최장 손실기간, 상위 10개 종목 기여도 확인.
5. 주문거부가 6,299건으로 많으므로 rejection 사유 분석 필요. 좋은 신호가 너무 많이 밀려나는 구조인지 확인.
6. 같은 source stream이 단독 1억원 운용에서 생성된 주문이라는 한계가 남아 있다. 완전 결합 시뮬레이터에서 공유자본 상태로 신호를 재생성해야 최종 확정 가능.

## 클로드 후속 요청

1. `+605.05%` 조합을 candidate 조합으로 재현하고, `persist_merged_run` 등록 여부는 검증 후 결정.
2. `V2/RECOVERY/V10`을 같은 데이터 스냅샷과 최신 `security_share_history` 기준으로 재실행해 `point_in_time_approx` 리스크를 낮출 것.
3. `V5`에 run_spec/아티팩트를 붙여 재생성하면 +643% 후보가 실제인지 다시 확인할 것.
4. 조합 탭에 “정식등록/후보/연구용” 세 등급을 나눠 표시할 것.
5. `+605.05%` 조합의 월별 수익률/MDD/상위 기여 종목을 프론트 리포트에 추가할 것.

