# 미국 지수 구성·가격 인프라 점검 (2026-09-27)

## 결론

- `NASDAQ` 3,173종목은 Nasdaq-100 지수가 아니라 Nasdaq 거래소의 현재 상장 종목 목록이다. 과거 백테스트에 그대로 사용하면 상장폐지·이전 종목이 빠져 생존편향이 생긴다.
- Nasdaq-100 시점별 구성 이력은 별도 `NASDAQ100` 지수로 적재했다. 공개 재구성 자료 112개 스냅샷, 305개 구간, 407개 편입·편출 이벤트이며 범위는 2007-02-01부터 2026-09-27 기준이다.
- Russell 2000도 정기 재구성과 수시 변경이 있다. `indexkit` 공개 자료를 검사했으나 2019-12~2026-06 N-PORT 자료 28개 파일은 티커가 전부 NULL이고 CUSIP만 있다. 2026-09 IWM 일별 자료부터 티커가 있으므로 기존 과거 가격에 자동 연결하지 않았다.
- 미국 일봉은 `us_price_history`에 4,189,826행, 3,708종목이 있고 최신일은 2026-09-25다. 일간 수집은 Yahoo 조정 OHLCV를 사용한다.
- `us_factor_snapshot`은 MA5·20·50·60·200, 52주 고저, 수익률, ATR, RS 등을 보유한다. MA50 누락과 종가 기반 52주 고저 계산을 수정하고 기존 일봉에서 재구축했다.
- 주봉 원본 테이블은 따로 두지 않는다. `aggregate_weekly_ohlcv()`가 일봉을 실제 주 마지막 거래일 기준 OHLCV로 결정론적으로 집계한다. 일봉을 단일 원본으로 유지해 조정 기준이 다른 중복 시계열이 생기는 것을 막는다.

## 적용 내역

- Nasdaq-100 실행: `usref_6fb7be84d7dd47b9951bc967a011dceb`
  - 소스 SHA-256: `c7de3905bfdd228eefbd3c7df1539a1178bee9006a04688fb319314a646e18e8`
  - 독립 조회 결과: 2021-09-27 102종목, 2024-12-23 101종목, 2026-09-26 101종목
- 기술 팩터 실행: `usfactor_e1c67e0ef5f74160aa9601dafe99f800`
  - 대상 3,705종목, 기존 팩터 행 3,673종목 갱신, 사후검증 통과
  - 백업 3,673행을 `us_factor_technical_backup`에 보존
  - 최종 팩터 3,675행: MA50 3,627, MA200 3,471, 실제 52주 High/Low 3,675

## 공개 코드·자료 판단

- 가격 수집은 이미 `yfinance`의 다종목 `download(..., interval="1d", auto_adjust=True)`를 사용한다. GitHub 구현은 일봉과 주봉(`1wk`)을 지원하지만, 운영에서는 검증된 조정 일봉을 저장하고 주봉을 내부 집계한다.
- Nasdaq-100은 `thuningxu/sp500nq100`의 시점별 CSV를 사용한다. 현재 구성과 변화표를 재구성한 공개 자료이며 공식 라이선스 피드가 아니므로 `public_reconstructed`로 표시한다.
- `kovagent/indexkit`은 소스 해시와 출처 기록이 잘 되어 있지만 Russell 과거 분기 자료에 거래 티커가 없다. CUSIP 시점별 식별자를 확보하기 전에는 백테스트 구성 이력으로 승격하지 않는다.
- Russell은 2026-09부터 IWM 보유종목을 일별 스냅샷으로 축적할 수 있다. 2019~2026 과거분은 라이선스된 지수 구성 데이터 또는 검증 가능한 CUSIP→당시 티커 매핑이 필요하다.

## 소스

- Nasdaq-100 공개 재구성: https://github.com/thuningxu/sp500nq100
- Russell/Nasdaq ETF·N-PORT 결합 구현: https://github.com/kovagent/indexkit
- yfinance: https://github.com/ranaroussi/yfinance
- FTSE Russell 공식 방법론·재구성: https://www.lseg.com/en/ftse-russell/indices/russell-us
