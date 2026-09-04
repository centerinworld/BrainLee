# Claude Handoff: Frontend Data Integrity Fixes (2026-07-13)

## 목적

프론트엔드에 서로 다른 백테스트 값, 검증되지 않은 전략 추천, 허위 저장/상태 표시,
불가능한 유통주식수, 중복 컨센서스, 수급 색상 불일치가 노출되던 문제를 정리했다.

## 이번 수정

### 1. 전략센터 백테스트 단일 원천화

- `StrategyHub`가 `/api/backtest/matrix`를 직접 조회한다.
- 화면 성과표, 평균, 양수 구간, 구간곱 참고값은 API 응답에서만 만든다.
- 기존 `PERIOD_RETURNS`는 `LEGACY_PERIOD_RETURNS`로 이름을 바꾸고 표시와 순위 계산에서 제외했다.
- 모든 결과에 `methodology_status=verified`와 `methodology.run_hash`가 있어야 자동 추천을 허용한다.
- 현재 149건 전부 `legacy_unversioned`, 유효 run hash 0건이므로 다음을 숨기거나 비활성화했다.
  - 현재 국면 추천 전략
  - 현재 국면 전략 우선순위
  - 전략 조합 수익률
  - 1억원 연속운용 순위
  - 하드코딩된 전략별 감사 성과 패널
- `승률`은 실제 거래 승률이 아니므로 `양수 구간`으로 변경했다.
- 독립 구간 수익률의 곱은 `누적수익` 대신 `구간곱 참고`로 변경하고 실계좌 수익이 아님을 표시했다.
- JSX에 직접 입력돼 있던 `+645%`, `+601%` 성과 문구는 검증 보류 문구로 교체했다.

### 2. 프론트 런타임 오류

- `loadCompanyQuantContext`를 `TradeAnalysis2` 내부로 이동했다.
- 미국주식 컴포넌트에 잘못 들어가 있던 동일 함수를 제거했다.
- `MegatrendView`의 미정의 `setStockCode` 호출을 `changeStock` prop으로 교체했다.
- `/api/screener/meta`를 실제 라우트 `/api/signals/meta`로 수정했다.

### 3. 설정 및 운영 상태

- 존재하지 않는 `/api/system/status` 대신 `/api/realtime/prices`에서 장 상태를 조회한다.
- 조회 전에는 `확인 중`, 실패 시에는 `조회 실패`를 표시한다.
- 실제 API 호출 없이 성공처럼 보이던 `설정 저장` 버튼을 제거했다.
- 데이터 소스와 수집 주기 컨트롤은 실제 설정 API가 생길 때까지 읽기 전용이다.
- 존재하지 않는 `/api/telegram/settings`는 편집 폼 대신 API 미연결 안내를 표시한다.
- 사이드바의 하드코딩된 `Operational` 문구를 제거했다.

### 4. 유통주식수

- `float_shares > shares_issued` 또는 `free_float_ratio > 100`이면 API에서 값을 `null` 처리한다.
- 품질 상태를 `review`로 바꾸고 표시 제외 사유를 기록한다.
- 프론트는 `partial`도 검토 대상으로 처리한다.
- 불가능한 값은 퍼센트를 표시하지 않고 `표시 보류 / 검증필요`를 표시한다.
- 프로필 재생성 스크립트도 발행주식수를 조금이라도 초과하면 `review`로 분류한다.

### 5. 컨센서스

- API 중복키를 `종목+보고일+증권사+애널리스트+목표가`로 통일했다.
- `report_idx`가 있는 행과 없는 행이 같은 보고서를 중복 집계하던 문제를 제거했다.
- 반복 연결된 보고서 제목을 API 응답에서 정리한다.
- 삼성전자 24개월 결과는 119건에서 77건으로 정리됐고 자연키 중복은 0건이다.

### 6. 수급 색상

- `MarketIndicatorsView`의 기관/외국인/개인 수급을 매수=녹색, 매도=빨간색으로 통일했다.
- 일별 막대그래프 설명도 같은 규칙으로 수정했다.

## 변경 파일

- `frontend/src/App.jsx`
- `frontend/src/views/MarketIndicatorsView.jsx`
- `main.py`
- `routes/consensus.py`
- `scripts/build_shareholder_profile.py`

## 검증 결과

- `npm run build`: 성공
- Python `py_compile`: 성공
- 수정 파일 ESLint `no-undef`: 0건
- `/api/signals/meta`: HTTP 200
- `/api/realtime/prices`: HTTP 200, `market_open` 포함
- 삼성전자 fundamentals: `float_shares=null`, `free_float_ratio=null`, `data_quality=review`
- 삼성전자 consensus: 77건, API 응답 자연키 중복 0건, 제목 반복 제거
- 브라우저 전략센터: `추천 보류`, `명세 미검증`, API 수치 표시 확인
- 브라우저 국내종목: `표시 보류`, `검증필요`, 잘못된 `(100.0%)` 미표시 확인
- 브라우저 수출입분석: 진입 후 `ReferenceError` 없이 렌더 확인

## Claude 필수 후속 작업

1. `backtest_run_specs`를 실제 실행 시 반드시 작성하고 아래 필드를 run hash에 포함할 것.
   - git commit, engine version, strategy params
   - signal timing, execution timing
   - point-in-time universe version, market-cap mode
   - allocation rule, initial capital, dynamic slot rule
   - fees, tax, slippage, price basis
2. 149개 레거시 결과를 동일 실행기로 재실행하고 `methodology_status=verified`로 승격할 것.
3. 연속운용 결과는 구간곱이 아니라 하나의 현금 원장으로 다시 계산할 것.
4. 전략별 1억원 계좌는 자산 증가 시 매수 가능 슬롯이 늘어나는 동적 자본배분 규칙을 적용할 것.
5. `LEGACY_PERIOD_RETURNS`, `CONTINUOUS_RETURNS`, `STRATEGIES.bestPeriod/auditTag`의 과거 수치는 검증 완료 후 삭제할 것.
6. `strategy-research/summary`의 ML Top20 `+424.4%` 등은 학습/검증 시계열 분리, 중복 종목, 라벨 누수, 생존편향을 별도로 재검증할 것.
7. `stock_shareholder_profile` 전체 재생성 후 품질 분포를 기록할 것. `stock_meta.float_shares`가 실제 유통가능주식인지 상장주식수인지 소스 정의를 확정해야 한다.
8. 컨센서스 원본 테이블에도 수집기 단계의 UPSERT 자연키를 적용해 중복 적재 자체를 중단할 것. 현재 수정은 API 집계 중복을 제거한 상태다.
9. 실제 스케줄러 설정 변경이 필요하면 인증된 GET/PATCH 설정 API를 만든 뒤 설정 화면을 다시 활성화할 것. API 키와 봇 토큰은 응답으로 반환하지 말 것.
10. 전체 프론트 ESLint 잔여 오류를 별도 정리할 것. 이번 수정 범위 파일 기준 no-undef는 0건이나 App의 미사용 변수, 빈 catch, hook 규칙 위반은 남아 있다.

## 채택 기준

전략센터의 추천과 순위는 다음 조건을 모두 충족하기 전까지 활성화하지 않는다.

- 모든 비교 셀에 동일한 run hash 구성 규칙 적용
- next-open 체결과 point-in-time 유니버스 사용
- 실제 현금/정수주식/거래비용 반영
- 구간별 결과와 연속운용 결과 분리
- 결과 재실행 시 허용 오차 내 재현
- 최소 1개 완전 홀드아웃 구간과 워크포워드 검증 통과
