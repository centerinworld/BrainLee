# Codex review: 텐베거 전략 로직 재검토

작성일: 2026-06-22

## 결론

Claude가 본 텐베거 계열 전략은 방향성이 좋다. 특히 `tb_hybrid`, `tb_base`, `tb_supply_plus`, `tb_value`는 “이미 오른 추세 추격”이 아니라 “낙폭과대 + 펀더멘털 변화 + 저평가 + 수급 반전”을 잡는 구조라서 텐베거 프로젝트 목적에 더 맞다.

다만 기존 결과를 그대로 실매매 승인하면 안 된다. 이유는 다음과 같다.

1. 결과 계보가 섞여 있다.
   - 스크린샷은 `scratch/tenbagger_bt_v5_results.json` 계열과 가장 유사하다.
   - `CLAUDE.md`에는 2026-06-22에 `tenbagger_backtest_v9.py` 기준이라고 적혀 있지만, 실제 `scratch/tenbagger_bt_v9_results.json`은 v5 스크린샷과 다르다.
   - v8/v9/현재 `tenbagger_engine.py` 주석에도 서로 다른 수치가 섞여 있다.

2. v5 백테스트의 MDD 계산에 결함이 있다.
   - `tb_hybrid`, `tb_base`, `tb_supply_plus`, `tb_value`의 `25.6~26.6`, `전체` MDD가 `-100%`로 찍힌다.
   - 이는 전략 자체가 전액 손실이라는 의미라기보다, 보유 종목의 당일 가격이 없을 때 포지션 가치가 누락되는 시뮬레이터 결함 가능성이 높다.

3. 현재 운영 엔진과 백테스트 기준이 일부 다르다.
   - `tenbagger_engine.py`는 최근 250일 종가 기준으로 52주 고가/저가를 계산한다.
   - 보수 검증 스크립트는 실제 `high/low` 기준으로 52주 고가/저가를 계산했다.
   - `high/low` 기준은 더 엄격하므로 후보가 줄고 수익률도 낮아진다.

## 기존 v5 결과 요약

원천 파일:

- `/Applications/stock_dashboard/scratch/tenbagger_bt_v5_results.json`
- `/Applications/stock_dashboard/scratch/tenbagger_backtest_v5.py`

주요 결과:

| 전략 | 평균 | 양수 기간 | 주요 특징 |
|---|---:|---:|---|
| `tb_hybrid` | +156.0% | 7/7 | 균형형. 수급 OR 낙폭+펀더멘털 |
| `tb_base` | +154.6% | 7/7 | 6축 기본 점수 합산 |
| `tb_supply_plus` | +138.1% | 7/7 | 수급 중심 + 낙폭과대 |
| `tb_value` | +147.1% | 7/7 | 저PBR/소형주 중심 |

주의:

- 위 수치는 v5 방식 기준이며 거래비용/가격누락/MDD 처리에 대한 재검증이 필요하다.
- `25.6~26.6`, `전체` MDD가 `-100%`로 찍히므로 v5 결과를 최종 성과표로 쓰면 안 된다.

## 보수 재검증 결과

새 검증 파일:

- `/Applications/stock_dashboard/scripts/research_tenbagger_logic_review.py`
- `/Applications/stock_dashboard/research_outputs/tenbagger_logic_review_results.json`
- `/Applications/stock_dashboard/research_outputs/tenbagger_logic_review_summary.csv`
- `/Applications/stock_dashboard/research_outputs/tenbagger_logic_review_picks.csv`

검증 변경점:

- 총 예산 1억원
- 1종목 슬롯 1천만원, 최대 10종목
- 매수/매도 거래비용 반영
- 보유 종목 가격 누락 시 마지막 가격 carry-forward
- 52주 고가/저가를 `high/low` 기준으로 보수 계산
- 후보 종목 로그 저장

결과:

| 전략 | 평균 수익률 | 양수 기간 | 최악 MDD | 상승장 | 하락장 | 회복 | AI랠리 | 최근 | 실전기 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `tb_supply_plus` | +36.64% | 4/6 | -47.25% | +99.53% | +35.56% | +4.25% | -28.72% | -16.38% | +125.59% |
| `tb_core_balance` | +26.43% | 4/6 | -48.84% | +125.50% | -8.83% | +24.66% | +3.19% | -14.15% | +28.19% |
| `tb_value` | +54.77% | 3/6 | -50.08% | +100.60% | +73.07% | -1.53% | -8.74% | -14.45% | +179.65% |
| `tb_base` | +50.31% | 3/6 | -48.41% | +126.22% | -10.26% | +6.41% | -10.69% | -18.67% | +208.86% |
| `tb_hybrid` | +47.40% | 3/6 | -48.41% | +126.22% | -10.26% | +6.41% | -8.13% | -16.03% | +186.19% |

해석:

- 보수 기준에서도 텐베거 계열은 완전히 무너지지 않는다.
- 그러나 기존 스크린샷 수준의 “전기간 고수익 안정 전략”으로 보기는 어렵다.
- `tb_supply_plus`가 가장 균형적이지만 AI랠리/최근 구간에서 약하다.
- `tb_value`, `tb_base`, `tb_hybrid`는 실전기 수익은 크지만 기간별 안정성이 낮다.

## 개선 후보

새로 테스트한 후보:

1. `tb_hybrid_quality`
   - 기존 `tb_hybrid`에 품질 게이트 추가
   - `fund >= 12` OR `value >= 12` OR `drawdown >= 22`
   - 결과가 `tb_hybrid`와 동일하게 나와, 현재 후보군에서는 필터가 추가 개선을 만들지 못했다.

2. `tb_core_balance`
   - 낙폭 -25~-75% 구간 필수
   - 펀더멘털/가치 중 하나 이상 강해야 진입
   - AI랠리에서 +3.19%로 양수 전환했지만 하락장/최근 구간이 약하다.

현재 최선의 방향:

- 단일 최강 전략으로 고정하지 말고, 레짐별로 텐베거 하위 전략을 전환한다.
- 하락장/약세장: `tb_supply_plus` 또는 `tb_value`
- 회복장: `tb_core_balance`
- 강세장/AI랠리: 텐베거 낙폭주만으로는 한계가 있으므로 별도 주도주/추세 전략과 혼합

## 추가 개선 제안

1. 엔진 주석/성과표 정리
   - `tenbagger_engine.py` 상단의 v8/v9/v5 수치 혼재를 정리해야 한다.
   - 현재 승인 가능한 수치는 “보수 재검증 통과 전 후보”로 표시한다.

2. 백테스트 표준화
   - v5, v8, v9를 통합한 `tenbagger_backtest_canonical.py` 필요.
   - 필수 조건:
     - 거래비용
     - carry-forward 가격
     - delisting/suspension 처리
     - 종가 기준 52주와 high/low 기준 52주를 둘 다 출력
     - 종목별 기여도/편중도 저장

3. 운영 로직 개선
   - 현재 `BUY_PARAMS`는 `min_score=50`, 낙폭 -30~-85%, 거래량 1.5배, 시총 5,000억 이하.
   - 실전 후보로는 `tb_supply_plus` 계열을 우선 검토하되, AI랠리/강세장에서는 별도 winner-pattern/주도주 전략과 병행해야 한다.

4. 실매매 승인 조건
   - 보수 검증 기준 6개 기간 중 최소 5/6 양수
   - 최악 MDD -35% 이내
   - 특정 종목 기여도 20% 이하
   - `^KS11` 2025~2026 지수 이상치 수정 후 재검증
