# Stockeasy 전략 3종 수집/추론 정리

기준일: 2026-04-26  
작성 목적: `Peak Easy / 모멘텀 Easy / 벨류 Easy`의 **종목 수집 로직(확정)**과, 현재 보유/이탈 데이터 기반 **선별 규칙(추론)**을 분리해서 기록

## 1) 확정된 수집 로직 (우리 시스템)

우리 시스템은 종목을 자체 선별하지 않고, Stockeasy 페이지를 모니터링하여 동기화합니다.

- 모니터링 URL
  - `https://stockeasy.intellio.kr/strategy-room/peak`
  - `https://stockeasy.intellio.kr/strategy-room/momentum`
  - `https://stockeasy.intellio.kr/strategy-room/value`
- 코드 근거
  - `peak_monitor.py`의 `STRATEGY_URLS`, `parse_strategy_page`, `run_once`
- 동작 방식
  - 전략 페이지의 **보유 테이블(현재가 포함)**, **이탈 테이블(매도가 포함)**를 파싱
  - 보유 종목:
    - DB에 이미 있으면 현재가/수익률 업데이트
    - 신규면 `/api/trend/buy`로 저장 (1종목당 1,000만원 기준 수량 계산)
  - 이탈 종목:
    - 사이트 보유 목록에서 사라지면 `/api/trend/sell` 처리
  - 즉, **선별(Selection)은 Stockeasy 서버**, **우리는 수집/동기화(Sync)**

## 2) 현재 DB 표본 (2026-04-26)

- 활성 보유
  - `momentum`: 22건
  - `peak`: 9건
  - `value`: 0건
- 매도 완료
  - 현재 `peak_holding` 기준 0건
  - `peak_trade`에서 momentum 매도 페어 1건 확인

## 3) 보유종목 기반 역추론 (기술지표 특징)

아래는 보유 종목의 `entry_date` 시점 가격데이터(`price_history`)로 계산한 공통 특징입니다.

### Peak Easy (표본 9, 신뢰도: 중)

- `종가 > MA20`: 100%
- `종가 > MA60`: 100%
- `MA20 > MA60 > MA120 > MA200`: 거의 완전 정배열(100%)
- `52주 고점 -15% 이내`: 100%
- 중앙값:
  - 5일 수익률 `+24.05%`
  - 20일 수익률 `+25.44%`
  - 52주 고점 괴리 `0.0%` (사실상 신고가권)

추론:
- Peak는 **강한 신고가 추세 추종(브레이크아웃형)** 성격이 매우 강함
- 실무용 추정 규칙:
  1. 장기 정배열(최소 MA20>MA60>MA120)
  2. 52주 고점 근접(통상 -15% 이내, 실제 표본은 더 타이트)
  3. 최근 20일 모멘텀 양수

### 모멘텀 Easy (표본 19*, 신뢰도: 중하)

`*` 22건 중 일부는 entry 시점 가격 시계열 부족으로 분석표본 19건

- `종가 > MA20`: 100%
- `종가 > MA60`: 94.7%
- `MA20 > MA60`: 73.7%
- `MA60 > MA120`: 84.2%
- `52주 고점 -15% 이내`: 73.7%
- 중앙값:
  - 5일 수익률 `+9.89%`
  - 20일 수익률 `+10.67%`
  - 52주 고점 괴리 `-8.79%`

추론:
- 모멘텀은 Peak보다 완화된 조건으로, **상승 추세 초중반 종목**까지 포함
- 실무용 추정 규칙:
  1. 기본 추세 유지(종가>MA20, 가능하면 종가>MA60)
  2. 20일 모멘텀 양수
  3. 신고가권 강제는 아니나, 고점 괴리 과대(-25~-30% 이하)는 비중 낮음

### 벨류 Easy (표본 0, 신뢰도: 낮음)

- 현재 활성 보유/매도 표본이 없어 데이터 기반 역추론 불가
- 추정만 하면:
  - Value 단독보다 `저평가 + 최소 추세 확인` 혼합형일 가능성
  - 하지만 현재 DB로는 검증 불가

## 4) 매수/매도 규칙 추론 요약 (현시점)

- Peak Easy
  - 매수: 신고가권 + 정배열 + 단기/중기 모멘텀 강세
  - 매도: 현재 표본 부족 (확정 불가)
- 모멘텀 Easy
  - 매수: 추세 유지 + 상대적으로 완화된 모멘텀 필터
  - 매도: 현재 확인된 1건은 약 `-8%` 구간 손절성 이탈
- 벨류 Easy
  - 데이터 부족으로 추정 보류

## 5) 결론

- **확정**: 우리 로직은 Stockeasy 전략 결과를 스크래핑/동기화하는 구조이며, 자체 선별 엔진이 아님
- **추론**:
  - Peak = 신고가 추세 추종 성향이 매우 강함
  - Momentum = Peak 대비 완화된 추세/모멘텀 필터
  - Value = 현재 데이터 부족으로 정량 추론 불가

## 6) 신뢰도/한계

- 샘플 한계:
  - Value 표본 0
  - 매도 표본 매우 적음
- 따라서 본 문서는 **운영 추론 문서**이며, 최종 규칙 확정 문서가 아님
- 매도 이력과 Value 표본이 누적되면 재추정 필요

## 7) 일일 반복 실행 루틴 (매수/매도 적중률 개선)

아래 순서를 매일 반복한다.

1. 기본 일치율 검증(트래커 자동 기록)
```bash
python3 /Applications/stock_dashboard/stockeasy_logic_validator.py --no-telegram
```

2. 편입일 매수 재현률(과거 1년) 확인
```bash
python3 /Applications/stock_dashboard/stockeasy_logic_validator.py --replay-entry --lookback-days 365
```

3. 매도 누적 백테스트(최근 30 스냅샷)
```bash
python3 /Applications/stock_dashboard/stockeasy_logic_validator.py --backtest-sell --sell-lookback-snapshots 30
```

4. 매수 파라미터 튜닝(모멘텀/벨류)
```bash
python3 /Applications/stock_dashboard/stockeasy_logic_validator.py --tune-strategy momentum --iterations 500
python3 /Applications/stock_dashboard/stockeasy_logic_validator.py --tune-strategy value --iterations 500
```

5. 매도 파라미터 튜닝(peak+momentum)
```bash
python3 /Applications/stock_dashboard/stockeasy_logic_validator.py --tune-sell --strategy peak --iterations 400 --sell-lookback-snapshots 30
python3 /Applications/stock_dashboard/stockeasy_logic_validator.py --tune-sell --strategy momentum --iterations 400 --sell-lookback-snapshots 30
```

6. 재검증(변경 반영 확인)
```bash
python3 /Applications/stock_dashboard/stockeasy_logic_validator.py --no-telegram
```

운영 기준:
- 당일 `compare` 점수만 보지 말고 `replay-entry`/`backtest-sell`를 함께 확인한다.
- 신규 편입 종목은 `replay-entry`에서 당일 편입 재현 성공 여부를 우선 점검한다.

현재 한계(2026-05-18 점검):
- 매도는 `hold_days/profit_pct + 당일 가격/수급`만으로는 F1 50% 도달이 어려움.
- 모멘텀/벨류도 현재 후보엔진 기준으로 F1 상한이 낮게 형성됨(과추출 다수).

다음 확장 항목(필수):
1. `exits_json.entry_stock_data` vs `exit_stock_data` 변화량(예: RSI, high52_pct, frn/inst 금액 변화) 피처화
2. 전략별 매도 분류기(peak/momentum 별도)로 재학습
3. 모멘텀/벨류는 공통 후보엔진이 아닌 전략별 별도 후보 생성기로 분리

## 8) 신규 편입/매도 발굴 중심 일일 점검 (필수)

목적:
- 보유/매도 정합성 확인을 넘어서, **다음날 신규 편입/매도 후보를 선제 발굴**한다.

매일 점검 항목:
1. 신규 편입 후보 발굴률
```bash
python3 /Applications/stock_dashboard/stockeasy_logic_validator.py --replay-entry --lookback-days 365
```
- `hit_rate`가 전일 대비 하락하면 후보 생성 로직 우선 수정

2. 신규 매도 후보 발굴률
```bash
python3 /Applications/stock_dashboard/stockeasy_logic_validator.py --backtest-sell --sell-lookback-snapshots 30
```
- `precision`이 낮으면 과신호 억제(후보 상한, score_cut 상향)
- `recall`이 낮으면 손절/익절 트리거 완화

3. 전략별 튜닝 반복
```bash
python3 /Applications/stock_dashboard/stockeasy_logic_validator.py --tune-strategy momentum --iterations 500
python3 /Applications/stock_dashboard/stockeasy_logic_validator.py --tune-strategy value --iterations 500
python3 /Applications/stock_dashboard/stockeasy_logic_validator.py --tune-sell --strategy peak --iterations 400 --sell-lookback-snapshots 30
python3 /Applications/stock_dashboard/stockeasy_logic_validator.py --tune-sell --strategy momentum --iterations 400 --sell-lookback-snapshots 30
```

4. 수정 후 재검증
```bash
python3 /Applications/stock_dashboard/stockeasy_logic_validator.py --no-telegram
```

운영 규칙:
- “보유 일치”보다 “신규 편입/신규 매도 발굴률”을 우선 KPI로 본다.
- 수치 악화 시 당일 로직 수정 후 재실행(최소 2회 반복)한다.

## 9) 사용자 확정 검증 규칙 (2026-05-20)

- 보유 종목 검증은 **오늘 시점**이 아니라 **실제 편입일(entry_date) 시점**으로 판정한다.
- 예: 20일 보유 종목이면 20일 전 데이터 기준으로 매수 신호가 재현되면 `정답`으로 본다.
- 매도도 동일하게 **실제 편출 발생일(as_of)** 기준으로 판정한다.
- 이미 `정답`으로 판정된 이벤트는 반복 점검 대상에서 제외한다.
- 매일 로직 개선 대상은 **미스 이벤트(못 맞춘 편입/편출)**만 남긴다.
