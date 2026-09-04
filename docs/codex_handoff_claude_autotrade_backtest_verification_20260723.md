# Codex Handoff — 자동매매/백테스트 검증 및 수정 2026-07-23

## 요약

클로드가 자동매매/백테스트 보강을 상당 부분 반영한 상태를 Codex가 재점검했다. 핵심 구조는 실제로 추가되어 있었다.

- 자동매매 생애주기 테이블: `live_orders`, `live_order_events`, `live_fills`, `live_cash_ledger`
- 리스크 게이트 테이블/API: `risk_gate_decisions`, `/api/kis-trading/risk-gates/*`
- 선택 run registry: `selected_run_registry`
- 검증 artifact: `run_verification_artifacts`
- 공용 포트폴리오/병합 시뮬레이터: `portfolio_engine.py`, `merged_simulator.py`

다만 전략센터 기본 API에 legacy run이 섞일 여지가 있어 Codex가 수정했다.

## Codex 수정 사항

### 1. 전략센터 매트릭스에서 legacy run 기본 제외

파일: `routes/backtest.py`

문제:

- `/api/backtest/matrix?include_legacy=false` 기본값에서도 `selected_run_registry`에 들어온 suite가 `legacy` 상태이면 기간 결과가 내려갈 수 있었다.
- 실제 점검 결과 `high_profit_compound`가 `legacy` 상태였다.
- 이 상태에서 프론트엔드가 실전 전략처럼 노출하면 백테스트 과신 위험이 있다.

수정:

- `verification_status == "legacy"`이고 `include_legacy=false`이면 해당 기간 결과를 건너뛰도록 차단.
- 빈 전략 엔트리는 제거.

확인:

```text
high_profit_compound absent
strategy_count 23
legacy_periods 0
```

### 2. 회귀 테스트 import 경로 보정

파일:

- `scripts/test_portfolio_engine.py`
- `scripts/test_strict_shared_simulator.py`

문제:

- 루트에서 직접 실행 시 `portfolio_engine`, `merged_simulator` import 실패.

수정:

- 테스트 시작 시 프로젝트 루트를 `sys.path`에 추가.

## 검증 결과

### Python 컴파일

명령:

```bash
/Applications/stock_dashboard/venv/bin/python -m py_compile routes/backtest.py scripts/test_portfolio_engine.py scripts/test_strict_shared_simulator.py routes/kis_trading.py
```

결과: 통과

### 포트폴리오 엔진 계약 테스트

명령:

```bash
/Applications/stock_dashboard/venv/bin/python scripts/test_portfolio_engine.py
```

결과:

```text
fixed: PASS
dynamic: OK (10→11 확장·거부·미실현확장 전부 통과)
ledger: OK (제로수익 왕복 비용 차감·원장 정합)
ALL PASS
```

### C3-C6 병합 시뮬레이터/보안마스터 회귀 테스트

명령:

```bash
/Applications/stock_dashboard/venv/bin/python scripts/test_strict_shared_simulator.py
```

결과:

```text
C3-C6 ALL PASS
```

### 프론트엔드 빌드

명령:

```bash
npm run build
```

작업 디렉터리: `frontend`

결과: 통과

참고:

- Vite chunk size warning은 남아 있으나 빌드는 성공.

### 전체 페이지 데이터 품질 감사

명령:

```bash
/Applications/stock_dashboard/venv/bin/python scripts/audit_all_page_data_quality.py
```

결과 파일:

- `research_outputs/all_page_data_quality_20260723.md`

요약:

```text
OK 29 / 수집필요 2 / 검토필요 1 / 누락 0
```

남은 이슈:

1. `broker_program_market_daily`
   - 상태: `needs_collection`
   - 기간: 2020-01-01 ~ 2026-07-22
   - 이슈: `contract:stale:1>0`
   - 해석: 2026-07-23 기준 전일 2026-07-22까지 있어 1일 stale. 장마감 후 오늘분 수집 필요.

2. `broker_program_stock_daily`
   - 상태: `needs_collection`
   - 기간: 2020-12-02 ~ 2026-07-22
   - 이슈: `contract:stale:1>0`
   - 해석: 종목별 프로그램 매매도 2026-07-23 오늘분 수집 필요.

3. `dilution_events.issue_amount`
   - 상태: `unstable_or_needs_review`
   - 행수: 17,736
   - 기간: 2016-05-25 ~ 2026-07-22
   - 금액 필드 채움: 12,029 / 17,736 = 67.82%
   - 해석: CB/BW/EB/유무상증자 이벤트 건수 기반 리스크는 사용 가능하나, 금액 기반 희석 리스크는 아직 부분완료.

## 선택 전략 run 검증 상태

`selected_run_registry(report_type='strategy_center')` 기준:

- `point_in_time_approx`: 15개
- `execution_strict`: 8개
- `legacy`: 1개

legacy:

- `high_profit_compound`

Codex 수정 후 기본 `/api/backtest/matrix`에서는 legacy 기간 결과가 내려가지 않는다. 감사 목적이면 `include_legacy=true`로 확인 가능.

## 클로드 후속 확인 요청

1. `high_profit_compound`를 계속 전략센터에 노출하려면 6기간 suite를 다시 만들고 최소 `execution_strict`까지 올릴 것.
2. 가능하면 `point_in_time_approx` 전략들을 `point_in_time_verified`로 승격할 수 있는지 확인할 것.
3. 2026-07-23 장마감 후 프로그램 매매 수집기를 재실행해서 `broker_program_market_daily`, `broker_program_stock_daily` stale 이슈를 제거할 것.
4. `dilution_events.issue_amount`는 80% 이상을 목표로 남은 `dart_disclosure_parse` 출처 금액 추출 보강을 계속할 것.
5. `risk_gate_decisions`는 현재 테스트성 `BUY_ALLOWED` 1건만 있다. 실제 후보 산출/페이퍼 주문 흐름에서 매수 차단 사유가 충분히 쌓이는지 모니터링할 것.
6. 자동매매 전에는 `BLOCKED_STALE_DATA`, `BLOCKED_RISK`, `WAIT_CONFIRM`, `SIZE_REDUCED`, `BUY_ALLOWED` 상태를 전략센터/후보군 화면에 노출하는지 확인할 것.
7. `live_cash_ledger`는 PAPER 모드 기준으로 기록된다. LIVE 전환 시 모드/계좌 현금 동기화 정책을 별도로 검증할 것.

## Codex 판단

클로드 수정은 방향이 맞다. 다만 실전 자동매매 관점에서는 `legacy` 결과를 숨기는 이번 수정이 필수였고, 프로그램 매매 당일분 stale 및 희석 금액 커버리지 부족은 계속 남은 리스크다.

현재 상태에서 실전 자동매매 허용 판단:

- 완전 자동 LIVE 매매: 아직 보류
- PAPER/가상매매: 가능
- 전략센터 실전 후보 표시: legacy 제외 조건하에 가능
- 백테스트 비교: `execution_strict` 이상만 신뢰, `point_in_time_approx`는 근사 결과로 표시

