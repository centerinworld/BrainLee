# StockEasy 로직 역추적 재검토 (Codex)

작성일: 2026-05-26
대상: `/Applications/stock_dashboard/stockeasy_logic_validator.py`
목적: Claude 발견 이슈 교차검증 + 추가 결함 탐지

## 핵심 결론
- 클로드가 지적한 "매도 신호가 비정상적으로 0" 현상은 **설정값(score_cut) + 게이트 조건(allow) + 경계값 비교(< vs <=)** 조합에서 충분히 재현 가능함.
- 특히 `value` 전략 매도 분기에는 실제로 **조건 불일치 버그**가 남아 있음.

---

## P0 (즉시 수정 권고)

1. `value` 전략에서 `primary_signal`이 `allow`에 반영되지 않음
- 위치: `/Applications/stock_dashboard/stockeasy_logic_validator.py:1786-1793`
- 현재 코드:
  - `allow = hard_loss or (trend_break and breakdown)`
- 문제:
  - `value` 분기에서 앞서 `primary_signal=True`로 세팅되는 케이스(예: `hold_days>=120 and profit_pct<=-28`)가 있어도,
    최종 게이트 `allow`에서 `primary_signal`을 보지 않기 때문에 **매도 후보가 누락**됨.
- 영향:
  - 장기보유 대손실 종목이 명시적으로 신호를 받아도 편출이 막힐 수 있음.
- 권고:
  - `value`도 `allow = primary_signal or hard_loss or (trend_break and breakdown)`로 통일.

---

## P1 (높음)

2. 손절 경계값 비교가 `<`로 되어 있어 임계값 정확히 일치 시 미발동
- 위치: `/Applications/stock_dashboard/stockeasy_logic_validator.py:1647`
- 현재 코드:
  - `hard_loss = hold_days >= 1 and profit_pct < loss_cut`
- 문제:
  - `profit_pct == -6.0`, `loss_cut == -6.0`이면 손절 미발동.
- 영향:
  - 스크린샷에서 언급된 "경계값 문제" 그대로 발생 가능.
- 권고:
  - `profit_pct <= loss_cut`로 변경.

3. `sell_value.score_cut=14` 설정이 `value` 신호 체계를 과도하게 경직
- 위치: `/Applications/stock_dashboard/config/stockeasy_logic_params.json`
- 확인값:
  - `sell_value.score_cut = 14`
- 문제:
  - `value`는 기본 신호 점수 합이 보통 4~8 수준이라, 게이트 조건 조금만 안 맞으면 영구적으로 매도 0이 되기 쉬움.
- 영향:
  - 실전에서 "편출 불능" 패턴 유발.
- 권고:
  - 구조 수정(allow 정합성) 후에도 백테스트 기준으로 cut 재튜닝 필요(예: 6~10 범위 탐색).

---

## P2 (중간)

4. 수급 계산에서 NULL 동시 존재 행만 집계 → 실제 수급 약화
- 위치: `/Applications/stock_dashboard/stockeasy_logic_validator.py:1553-1556`
- 현재 코드:
  - `frn_net_buy_amt IS NOT NULL AND inst_net_buy_amt IS NOT NULL`
- 문제:
  - 한쪽만 NULL인 행은 전체 제외되어 5일 수급 합계 왜곡 가능.
- 권고:
  - 컬럼별 `COALESCE` 집계로 변경해 부분 결측 허용.

5. 동일 종목명 다중 매핑 리스크
- 위치: `/Applications/stock_dashboard/stockeasy_logic_validator.py:1491-1494`
- 현재 코드:
  - 종목코드를 `stock_name`으로만 조회 + `base_date DESC LIMIT 1`
- 문제:
  - 동명 이슈/개명/합병 이력 시 잘못된 코드로 지표 산출 가능.
- 권고:
  - 보유 데이터에 `stock_code`가 있으면 코드 우선 사용, 이름은 fallback으로 제한.

---

## 클로드 전달용 체크리스트

1. `value allow`에 `primary_signal` 반영 여부 확인
2. `hard_loss` 경계값을 `<=`로 처리했는지 확인
3. `sell_value.score_cut=14` 유지 근거(백테스트 P/R/F1) 재검증
4. 수급 집계 NULL 처리 개선 여부 확인
5. 종목 식별을 `stock_code` 우선으로 바꿨는지 확인

---

## 참고 증거
- `DEFAULT_PARAMS` 및 sell 설정: `/Applications/stock_dashboard/stockeasy_logic_validator.py:72-137`
- 매도 피처 추출: `/Applications/stock_dashboard/stockeasy_logic_validator.py:1478-1582`
- 매도 통과 게이트: `/Applications/stock_dashboard/stockeasy_logic_validator.py:1784-1795`
- 실제 운영 파라미터(`sell_value.score_cut=14`): `/Applications/stock_dashboard/config/stockeasy_logic_params.json`
