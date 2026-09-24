# Codex 독립 데이터 무결성 재검토 - 2026-09-06

## 결론

- 현재 사이트가 정의한 데이터 계약은 34/34 정상이며, 수집 필요 0, 검토 필요 0, 테이블 누락 0이다.
- 이 결과를 "모든 투자 데이터가 존재하고 모든 값이 무결점"이라는 의미로 확대하면 안 된다.
- 대시보드 조회와 연구용 핵심 데이터는 조건부 신뢰 가능하다. 실전 자동주문은 PIT 전진검증과 주문 직전 호가/거래제한 확인이 부족하므로 계속 비활성화가 맞다.
- Claude가 수행 중인 DART 원문 재조회와 겹치지 않도록 이번 감사는 독립 소스 대조, 키 유일성, 산식 불변식, 기간 연속성, 전략 입력 가용성에 집중했다.

## 독립 검증 결과

- 최근 `price_history` 대 `stock_price_daily` 중첩 26,684건의 종가 불일치는 0건이었다. 거래량 차이는 10건이었다.
- 거래가 발생한 국내 주식의 OHLC 불변식 위반은 0건이었다. 발견된 2,153건은 거래량 0인 거래정지/무거래 행의 종가 유지 표현이었다.
- `price_history`의 주말 행은 환율, 지수, 원자재 계열이며 국내 종목의 휴일 시세 오염으로 보지 않는다.
- KIS 대 Kiwoom 최근 수급 64,056건의 상관계수는 기관 1.00000, 외국인 0.99999였다.
- 외국인 보유 독립 소스 10,673건은 비중 기준 전부 허용오차 내 일치했다. 수량 차이는 SK하이닉스 1건이며 비중 차이는 0.01%p였다.
- 핵심 일별 테이블의 종목-날짜 중복 키는 0건이었다.
- 투자자 매수-매도=순매수 산식 4,519,957건의 위반은 0건이었다.
- 공매도/대차 3,904,773건의 음수 값 위반은 0건이었다.
- 발행주식수 이력 95,286건은 음수/0, 역전 기간, 실제 기간 중첩이 모두 0건이었다.
- 공시 가용일 41,894건은 NULL, 공시일 NULL, 결산기간보다 이른 가용일이 모두 0건이었다.

## 이번 수정

- 시장 외국인 수급 교차검증 원천을 폐지된 공공 API의 `investor_trading_daily`에서 Kiwoom `ka10059` 순매수 금액으로 교체했다.
- 완전 결측 재무행을 `canonical_financial_data`에 올리지 않도록 `EMPTY_FINANCIAL_ROW` write gate를 추가했다.
- 기존 canonical 완전 결측 133행을 `canonical_financial_data_backup_20260906_empty_rows`에 백업한 뒤 제거했다. 원천 `financial_data`는 변경하지 않았다.
- 전체 페이지 감사에 canonical 완전 결측 1건도 실패로 처리하는 규칙을 추가했다.
- 재무 write gate 회귀 테스트 6개, Python 컴파일, `git diff --check`를 통과했다.
- 서비스 재시작 후 핵심 API는 HTTP 200, 0.112초였고 PostgreSQL 16.15 및 canonical 완전 결측 0건을 확인했다.

## 남은 범위

- `investor_trading_daily`는 2026-07-10, `foreign_holding_daily`는 2026-06-08 이후 중단됐지만 각각 Kiwoom `ka10059`, `ka10008`로 대체되어 현재 페이지 계약은 충족한다.
- 실적발표 예정 캘린더, 과거 지수 편입/리밸런싱 이력, 회원사별 매매 집중도는 현재 PostgreSQL 스키마에 없다. 이는 기존 34개 계약 밖의 미수집 도메인이다.
- `orderbook_snapshots`는 1종목 5건, `trading_restrictions`는 26종목이며 최종 시점은 2026-08-13이다. 실전 주문 전에는 전 종목 상시 수집보다 주문 후보에 대한 즉시 재조회 및 실패 차단이 필요하다.
- 미래 구간 전진검증 및 완전한 point-in-time 증거가 부족하므로 전략센터 실전 자동매매 판정은 계속 차단 상태로 유지한다.

## 재현 자료

- `research_outputs/all_page_data_quality_20260906.md`
- `research_outputs/all_page_data_quality_20260906.json`
- `research_outputs/strategy_center_live_data_readiness_latest.md`
- `research_outputs/strategy_center_live_data_readiness_latest.json`
