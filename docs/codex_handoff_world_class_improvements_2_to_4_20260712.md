# Codex → Claude 핸드오프: 세계 최고 수준 개선안 2~4 진행

작성일: 2026-07-12

## 2. 시점 일치 데이터베이스

신규:

- `point_in_time.py`
- `scripts/build_data_availability_ledger.py`
- `data_availability_ledger`
- `strict_backtest_runs`

가용시점 원장 206,791건:

- 재무 exact disclosure: 41,894
- 원재료 exact disclosure: 4,789
- 수주잔고 exact disclosure: 1,735
- 퀀트지표 fallback lag: 158,373

`validate_strict_trade_contract()`는 다음을 위반으로 처리한다.

- feature available date가 signal date보다 늦음
- 장 마감 후 신호를 당일 체결
- same close 등 비엄격 체결가격

DART 주간 배치 성공 후 availability ledger를 자동 갱신한다.

Claude 검증:

1. 수주잔고·원재료 공시일 다음 영업일 계산 표본 대조
2. 퀀트지표 fallback 발표일 규칙을 소스별 실제 발표일로 교체
3. `backtest.py`의 당일 종가 체결 경로를 next open/next close로 순차 교체
4. 모든 결과 저장 전에 strict contract assertion 적용

## 3. 가설 연구 표준 계약

신규:

- `research_governance.py`
- `scripts/initialize_research_governance.py`
- `hypothesis_research_registry`
- `hypothesis_research_runs`

필수 그룹:

- identity
- sample
- performance: total return, CAGR, MDD, positive rate, profit factor
- execution: initial capital, price basis, execution type, costs, slippage
- validity: OOS, lookahead, fallback rows, survivorship control

낙폭과대 연구는 판정은 `rejected`이나 CAGR·MDD·PF·초기자금·비용·슬리피지가 없어 `needs_completion`으로 등록됐다.

Claude 검증:

1. 현재 프론트 리포트에 `needs_completion` 배지 연결
2. 표준 필드가 없는 연구는 headline 성과 노출 금지
3. 모든 연구 결과를 registry/run 구조로 이관

## 4. 현금·복리 포트폴리오 엔진

신규:

- `portfolio_engine.py`
- `scripts/test_portfolio_engine.py`

지원:

- 실제 보유 현금 한도
- 정수 주식 수
- 최대 동시 보유 종목
- 매도대금과 수익의 복리 재투자
- 수수료·슬리피지
- 매수·매도 ledger
- equity, total return, win rate, fees

검증:

- 1억원 전액 매수
- 10% 수익 매도 후 현금 1억1천만원
- 늘어난 현금으로 1천1백만원씩 10개 포지션 매수
- 최종 equity 1억1천만원 확인

남은 연결:

1. 전략센터 모든 백테스트의 자체 자금 계산을 공통 엔진으로 교체
2. 매도 신호를 먼저 처리한 뒤 같은 날 신규 매수 처리
3. strict next-bar execution 연결
4. 거래정지·상하한가·거래량 부족 체결 실패 추가
5. 전략별 기존 결과와 신규 공통 엔진 결과 diff 생성

## 전체 상태

- 1번 수정주가·자본행위: 기반 및 외부 검증 완료, 미해결 급변 추가 정리 중
- 2번 시점 일치: 공통 원장·검증 계약 완료, 개별 백테스트 이관 필요
- 3번 연구 표준: 계약·레지스트리 완료, 기존 연구 이관 필요
- 4번 포트폴리오: 공통 엔진 완료, 전략별 연결 진행 중
- 5~10번: 아직 구현 전

