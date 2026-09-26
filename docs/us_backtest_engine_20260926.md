# 미국 주식 전용 백테스트 엔진

## 분리 이유

`backtest_common.py`는 한국 6자리 종목코드, `price_history`, KRX 가격제한폭,
한국 수수료·거래세, DART 공시 지연과 `stock_universe`를 전제로 한다. 미국 주식에
재사용하면 시장 달력·수수료·공시 시점·상장폐지 처리와 유니버스가 섞인다. 그래서
`us_backtest_common.py`는 한국 엔진을 import하지 않고 `us_*` 테이블만 읽는다.

## 현재 데이터 계약

- 가격: `us_price_history`, 2021-05-24~2026-09-25, 3,670 ticker, 4,045,930행
- 가격 기준: 수집기가 `yfinance(..., auto_adjust=True)`로 저장한 분할·배당 보정 OHLC
- 재무: `us_financial_data`; 신호일에 `avail_date <= signal_date`인 행만 사용하며
  `avail_date`가 없는 행은 제외
- 메타: `us_stock_meta`의 S&P 500/NASDAQ 구성은 **현재 구성**이다. 과거 구성 이력이
  아니므로 이를 이용한 결과는 항상 `survivorship_bias=true`, `research_grade=false`다.
- `adj_close`가 별도로 없으므로 엔진이 가격 급변만 보고 분할을 추정하거나 값을
  이어 붙이지 않는다. 설정 배율을 넘는 단절은 품질 이벤트로 기록한다.

## 체결 순서

1. D일 장 마감 후, D일까지의 이력만 전략 콜백에 전달한다.
2. 목표 비중 주문을 대기열에 넣는다.
3. 다음 시장 세션 D+1의 시가에 매도 후 매수한다.
4. D+1 시가가 없는 종목은 종가로 대체하지 않고 주문을 거절한다.
5. 체결 후 D+1 종가를 이력에 공개하고 일별 평가액을 저장한다.

기본 비용은 5bp 슬리피지이며 주당 수수료·최소 수수료·매도 명목금액 수수료를
설정할 수 있다. 규제 수수료는 시기별로 바뀌므로 엔진에 특정 연도의 요율을
하드코딩하지 않았다.

## 사용법

```bash
./venv/bin/python us_backtest_common.py \
  --start 2022-01-03 --end 2026-09-25 --index S\&P500 --top 5
```

기본 예시는 126거래일 모멘텀 상위 5종목을 월초에 재조정하고, 종가가 200일
이동평균 위인 종목만 고른다. `--persist`를 명시한 경우에만 한국 테이블과 분리된
`us_backtest_runs`, `us_backtest_trades`, `us_backtest_equity`에 원자적으로 저장한다.

## 오픈소스 검토

- [QuantConnect LEAN](https://github.com/QuantConnect/Lean): 기업행위·주문·시장 모델이
  가장 완전하지만 C#/Docker 중심의 별도 런타임과 데이터 변환이 필요하다.
- [bt](https://github.com/pmorissette/bt): 재조정·비용 모델 구조를 참고하기 좋지만
  현재 PostgreSQL의 공시 가능일과 과거 유니버스를 해결하지 않는다.
- [vectorbt](https://github.com/polakowo/vectorbt): 대규모 파라미터 탐색에 적합하지만
  이벤트 시점·상장폐지 품질 게이트는 별도 구현이 필요하고 현재 환경에 설치돼 있지 않다.
- [backtesting.py](https://github.com/kernc/backtesting.py): 단일 종목 전략에는 간단하지만
  다종목 시점별 유니버스에는 맞춤 계층이 필요하며 라이선스도 배포 방식과 함께 검토해야 한다.
- [QuantStats](https://github.com/ranaroussi/quantstats): 실행 엔진이 아니라 결과 분석
  라이브러리다. 일별 평가곡선이 안정화된 뒤 보고서 계층에 적용할 수 있다.

외부 엔진을 그대로 들여오지 않은 이유는 현재 병목이 주문 반복문의 부재가 아니라
과거 구성종목·상장폐지 수익률·가격 원천 계약의 부재이기 때문이다. 이 데이터 문제를
외부 패키지는 자동으로 해결하지 못한다.

## 검증과 남은 데이터 과제

- 단위 테스트는 당일 종가 체결 금지, 다음 시가 결측 거절, 목표비중 재조정,
  생존편향 표시, 종료 미청산 표시, 가격 단절 표시, 한국 테이블 비참조를 검사한다.
- 2022-01-03~2026-09-25 전체 현재 S&P 500 기준 실행은 기술적으로 완료되지만
  현재 구성종목을 과거 전체에 적용하므로 성과 숫자는 연구 결론으로 사용할 수 없다.
- 엔진은 SPY 벤치마크를 지원하지만 현재 `us_price_history`에는 SPY가 없으므로 기준
  실행은 `benchmark_available=false`다. SPY 원장을 적재하기 전에는 초과수익을 내지 않는다.
- 연구 등급으로 올리려면 날짜별 S&P 500/NASDAQ 구성 이력, 상장폐지·합병 ticker의
  최종 현금/주식 교환 수익, ticker 변경 이력과 원천별 보정계수를 적재해야 한다.
- 수수료 민감도와 세금은 투자자·브로커·연도에 따라 다르므로 전략 실행 시 명시한다.
