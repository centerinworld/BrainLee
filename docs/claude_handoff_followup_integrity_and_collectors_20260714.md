# Claude Handoff: Follow-up Integrity and Collectors (2026-07-14)

이 문서는 `claude_handoff_frontend_data_integrity_fixes_20260713.md`의 후속이며,
아래 완료 수치와 제한사항을 최신 기준으로 사용한다.

## 완료한 수정

### 1. 백테스트 실행 명세와 프론트 게이트

- `backtest.py::_run_generic_backtest`가 실행 시작 시 `backtest_run_specs`를 기록한다.
- 명세에는 엔진/함수/기간/매개변수, D 종가 신호, D+1 시가 체결, 시총 기준,
  동적 슬롯, 유니버스 설명, 비용 모델과 run hash가 포함된다.
- `/api/backtest/matrix`는 명세 존재만으로 검증 완료 처리하지 않는다.
  - `verified`: next-open + point-in-time 유니버스 + 필수 명세 충족
  - `specified_unverified`: run hash는 있으나 위 조건 미충족
  - `legacy_unversioned`: 실행 명세 없음
- 전략센터의 추천/조합/연속운용 성과는 모든 관련 결과가 `verified`일 때만 열린다.
- 현재 서비스 API 기준 149개 비교 셀은 전부 `legacy_unversioned`이며 경고 149건이다.

### 2. 컨센서스 중복 원인 제거

- 자연키를 `종목+보고일+증권사+애널리스트+목표가`로 통일했다.
- 수집기는 같은 자연키가 있으면 기존 행을 갱신한다.
- 반복 연결된 제목은 첫 종목코드 앵커를 사용해 정리한다.
- 기존 11,551행에서 중복 3,704행을 제거해 7,847행으로 정리했다.
- `ux_consensus_targets_natural` 고유 인덱스를 생성했고 잔여 자연키 중복은 0건이다.
- 삭제 대상 원본은 `consensus_targets_duplicate_backup_20260714`에 보관했다.
- 정리 리포트: `research_outputs/consensus_duplicate_cleanup_20260714.json`
- `/api/consensus/recent`를 동적 `/{stock_code}`보다 먼저 등록해 `recent`가 종목코드로
  해석되던 라우팅 오류를 수정했다.

### 3. DART 임원/최대주주 수집

- `majorstock.json`에 필수 `corp_code`가 빠져 있던 수집기를 종목별 조회로 변경했다.
- 전 종목 최대주주 백필: DART 매핑 2,608종목 요청, 21,054건 처리, API 오류 0건.
- 전량 및 서비스 재시작 증분 반영 후 테이블: 21,119행, 2,326종목,
  접수일 2024-07-15~2026-07-14.
- `stock_shareholder_profile` 2,693종목 재생성 결과:
  - `ok` 1,290
  - `review` 1,169
  - `partial` 232
  - `missing_float` 2
  - 최대주주 표시 가능 2,323종목
- 일일 증분도 최근 공시 종목의 `corp_code`별 조회로 변경했다.
- DART가 날짜 파라미터를 무시하고 기업 전체 이력을 반환하는 경우가 있어,
  응답의 `rcept_dt`를 저장 전에 다시 검사한다.
- 최근 2일 증분 실증: 임원 153건, 최대주주 70건, 오류 0건.
- 주간 스케줄은 임원 시총 상위 2,200개 제한을 제거하고 전 종목을 조회한다.
- 기존 임원 데이터에 없던 331종목도 전부 조회해 442건을 반영했고,
  임원 공시 보유 종목은 2,400개에서 2,485개로 증가했다(나머지는 조회했으나 공시 없음).
- 주간 작업 종료 후 최대주주까지 수집한 뒤 주주 프로필을 재생성한다.
- 프로필 재생성 DB 연결에는 60초 잠금 대기를 적용해 동시 스케줄 작업과의 충돌을 완화했다.

### 4. 유통주식 품질

- 개별 종목뿐 아니라 `/api/dashboard/shareholder-profiles` 목록에서도
  `float_shares > shares_issued` 또는 비율 100% 초과 값을 `null`로 숨긴다.
- 삼성전자처럼 원천값이 불가능한 종목은 `review`, `표시 제외` 사유를 반환한다.

## 검증

- 프론트 `npm run build`: 성공
- 수정 Python 파일 `py_compile`: 성공
- `scripts/test_portfolio_engine.py`: ALL PASS
  - 1억원, 10개 슬롯 시작
  - 수익 10% 도달 시 11번째 슬롯 확장
  - 현금 부족 주문 거부와 비용 원장 정합성 확인
- 서비스 재시작 후:
  - `/api/consensus/recent`: 정상 목록 반환
  - `/api/backtest/matrix`: 149건 모두 명세 없는 레거시로 차단
  - `/api/dashboard/shareholder-profiles?q=005930`: 불가능한 유통주식수 미노출

## 남은 핵심 제한사항

### P0. point-in-time 유니버스가 아직 없다

- `security_master_history`, `stock_universe_history` 테이블이 없다.
- `stock_price_daily`는 836,594행이지만 날짜 커버리지가 불연속이다.
  - 2020년 248일
  - 2021년 31일
  - 2022~2025년 0일
  - 2026년 64일
- 따라서 현재 종목 멤버십을 과거에 투영하는 생존편향을 제거할 수 없다.
- 이 공백이 해결되기 전에는 어떤 레거시 성과도 `verified`로 올리지 말 것.

### P0. 레거시 백테스트 재실행

- 현재 완료 런 2,079개 중 완료 런과 연결된 명세는 0개다.
- 새 실행기는 명세를 기록하지만 과거 결과에 명세를 사후 부착하면 안 된다.
- PIT 유니버스 구축 후 동일 엔진으로 6개 구간과 2020-03 이후 연속 계좌를 다시 실행한다.
- 재실행 완료 전 `LEGACY_PERIOD_RETURNS`, `CONTINUOUS_RETURNS` 하드코딩 값은
  화면 채택 근거로 사용하지 말고 최종적으로 삭제한다.

### P1. 유통주식 원천 정의

- 1,169종목의 `stock_meta.float_shares`가 발행주식수보다 커 실제 유통가능주식으로
  사용할 수 없다. 현재는 노출만 차단한 상태다.
- 최대주주 한 명의 최신 공시만으로 유통주식수를 계산하면 다른 5% 주주와 특수관계인,
  자사주를 누락하므로 임의 계산하지 말 것.
- KRX/KIND/DART 원천의 유통가능주식 정의를 확정하고 기준일 기반 스냅샷 테이블을 만든다.

### P1. DART 이력 범위

- 이번 `majorstock` 응답에서 확보 가능한 최초 접수일은 2024-07-15였다.
- 2020~2024 구간은 API 응답 보존 범위 밖일 가능성이 있으므로 공시 원문 목록 기반의
  별도 역사 백필 가능성을 확인해야 한다.

## Claude 확인 순서

1. 위 P0 데이터 공백을 먼저 해결한다.
2. 새 런의 `backtest_run_specs`와 결과 행을 run id로 대조한다.
3. `verified` 승격 전에 next-open, 정수주식, 실제 현금, 비용, 동적 슬롯을 샘플 거래로 재계산한다.
4. PIT가 없는 결과가 프론트 추천/순위/조합/연속운용에 다시 나타나지 않는지 확인한다.
5. 최대주주와 유통주식은 기준일과 출처를 함께 표시하고 `review` 값을 투자 신호에 쓰지 않는다.
