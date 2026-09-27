# Minervini·전략센터 후속 작업 지시서 (2026-09-27)

## 먼저 고정해야 할 현재 상태

- Minervini 미국 PIT 결과는 연구용이다. S&P 500 PIT는 상장폐지·인수 종목 39개의 일부 가격이 없고, Nasdaq-100 PIT는 2007년부터 적용됐으며, Russell 2000 과거 PIT는 아직 없다.
- `selected_strategy_price_integrity_latest.json` 최신 결과는 27개 중 22 통과, 3 실패(`deep_recovery`, `low_base_breakout`, `v8`), 2 거래증거 없음(`v10`, `v11`)이다. `hermes.md`의 과거 26/1 기록과 현재 suite hash가 다르므로 최신 run hash 기준으로 다시 정합화해야 한다.
- `selected_strategy_data_availability_latest.json`은 162개 컴포넌트 중 48 통과, 114 실패다. 실패 사유는 신호가 소비한 지연 데이터의 행 ID·값·`available_at`이 거래별로 저장되지 않았기 때문이다.
- `backtest_static_contracts_latest.json`은 P0 1건, P1 29건이다.
- 전방검증(`forward_validated`) 전략은 0개다. 실행 준비도 감사는 오래된 2026-09-08 산출물이므로 현재 코드·DB로 재실행해야 한다.

## 작업 A — Minervini를 채택 가능한 연구 전략으로 만들기

1. **유니버스 고정**: 우선 Nasdaq-100 PIT(2007+)를 정본으로 사용하고 QQQ를 벤치마크로 사용한다. S&P 500은 누락된 상장폐지 가격·종료가치가 채워질 때까지 별도 민감도 결과로 유지한다. Russell은 검증된 CUSIP→당시 티커 매핑 전까지 사용하지 않는다.
2. **전략 명세 버전 고정**: Trend Template, SEPA, VCP를 각각 독립 모듈로 유지하고 최종 조합의 모든 임계값을 JSON 명세와 해시로 저장한다. 파라미터 탐색 결과와 채택 결과를 분리한다.
3. **RS 개선**: SPY 고정 3개월 초과수익 근사 대신 PIT 유니버스 내 12/6/3/1개월 가중 상대강도 백분위와 QQQ 대비 상대강도를 모두 기록한다.
4. **주봉·VCP 검증**: 저장된 조정 일봉에서 주봉을 생성하고 수축 횟수, 각 수축 깊이, ATR 수축, 거래량 건조, 피벗, 돌파를 신호일 현재까지의 데이터로만 계산한다. 미래 고점·저점을 패턴 확정에 사용하지 않는다.
5. **재무 조건 보강**: SEC 실제 `avail_date`를 사용하고 EPS·매출 성장, 성장 가속, 마진·ROE를 거래별 입력 provenance로 저장한다. 애널리스트 추정치가 없으면 SEPA의 추정이익 조건은 미구현으로 명시한다.
6. **체결 현실화**: 신호 D 종가, D+1 시가 체결을 유지하고 거래정지, 갭, 상장폐지, 인수대금, 거래대금 대비 주문비중, 스프레드·시장충격 비용을 적용한다.
7. **검증**: 고정 학습 구간, 미사용 시험 구간, rolling walk-forward를 분리한다. Trend/SEPA/VCP의 ablation, 임계값 민감도, 부트스트랩 신뢰구간을 함께 낸다.
8. **전방 표본**: 백테스트가 통과하면 별도 shadow 계좌에서 최소 60일과 완결거래 20건을 모은다. 그 전에는 `forward_validated`로 승격하지 않는다.

### Minervini 완료 기준

- `survivorship_bias=false`, `pit_reference_complete=true`
- 신호일별 가격 커버리지 99% 이상, 모든 보유종목 종료가치 처리
- 잘못된 OHLC 0건, 미검토 대형 점프 0건
- 모든 체결이 다음 거래일 시가이며 close fallback 0건
- 거래별 재무 입력 row ID와 `avail_date` 100% 저장
- 미사용 시험구간과 shadow 원장 결과가 생성되고, 프론트엔드가 연구·PIT·전방검증 상태를 구분 표시

## 작업 B — 전략센터 전체 백테스트 기반 보강

1. **최신 suite 정합화**: 선택 registry, 6구간 run hash, 가격 감사, 데이터 가용성 감사, 기업행위 감사가 같은 suite hash를 가리키게 한다. 하나라도 오래되면 성과를 숨기고 `revalidation_required`로 표시한다.
2. **감사 재실행**: 최신 27개 전략으로 가격·상장구간 감사를 다시 실행한다. `deep_recovery`, `v8`의 실제 상장구간·거래정지 진입을 조사하고, `low_base_breakout`의 SPAC 전신 가격은 현재 회사에 연결하지 않는다. `v10`, `v11`은 거래 원장 생성 실패 원인을 고친 뒤 재감사한다.
3. **입력 provenance**: 114개 실패 컴포넌트에 대해 신호가 사용한 재무·수급·공시·섹터 입력의 원본 행 ID, 값, `available_at`, source hash를 거래마다 저장한다.
4. **공통 실행 엔진**: 백테스트·paper·live가 같은 signal adapter와 파라미터 명세를 호출하게 한다. 현재가 체결과 D+1 시가 체결, 포지션 수, trailing stop, 공시 유효기간의 드리프트를 제거한다.
5. **어댑터 완성**: 전략센터의 모든 채택 전략에 현재일 신호 어댑터를 만들되, 백테스트 신호와 동일 결과를 내는 golden fixture를 요구한다. 전체 과거를 매일 다시 돌려 최신 거래만 뽑는 repaint 방식은 제거한다.
6. **원장·비용·리스크**: 고아 매도, 음수 현금, 마지막 가격 강제 청산을 차단한다. 수수료·세금·슬리피지·거래대금 참여율·호가 가능 여부를 공통 주문 게이트에 둔다.
7. **평가 체계**: 6개 장세 평균만으로 선정하지 말고 CAGR, MDD, Sharpe/Sortino, turnover, capacity, benchmark alpha, 월별·연도별 안정성, 파라미터 민감도를 저장한다.
8. **전방검증 큐**: PIT·가격·가용성·실행 게이트를 통과한 전략만 shadow 계좌에 등록한다. 최소 60일·완결거래 20건 이후에만 `forward_validated` 후보가 된다.
9. **프론트엔드**: 하드코딩 수익률은 제거하고 선택 registry 결과만 표시한다. 유효하지 않은 run, 감사 시점이 오래된 run, 재무 재계산 이후 미재검증 run은 수익률과 최고전략 배지를 숨긴다.

### 전략센터 완료 기준

- 정적 감사 P0=0
- 선택 전략 전부 가격/상장구간 감사 결과 또는 명시적 `no_trade` 사유 보유
- 지연 데이터 소비 거래의 provenance 100%
- 백테스트와 paper 신호 golden fixture 100% 일치
- 고아 매도·음수 현금·close fallback 0건
- PIT 통과 전략만 shadow 계좌 생성
- 전략별 데이터 기준일, suite hash, 검증등급, 비용 포함 성과가 API와 화면에서 동일

## 권장 세션 분리

1. **감사 정합성 세션**: 최신 suite hash 재등록, 3 실패·2 무증거와 114 provenance 실패 해결.
2. **공통 실행 엔진 세션**: signal adapter와 체결·비용·원장 통합, golden fixture 구축.
3. **Minervini 연구 세션**: Nasdaq-100 PIT, QQQ/RS, 주봉 VCP, walk-forward 재실행.
4. **전방검증·UI 세션**: shadow 계좌 생성 규칙과 전략센터 검증 상태 표시.

각 세션은 다른 세션의 미커밋 파일을 포함하지 말고, 시작 시 최신 감사 산출물의 `checked_at`과 suite hash를 다시 확인한다.
