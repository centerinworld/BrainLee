# 전략센터 실전매매 데이터 보강 핸드오프

## 결론

- 2026-08-13 22:37 기준 실전 매수는 `BLOCKED` 상태를 유지한다.
- 실전 주문을 활성화하지 않았다. `/api/kis-trading/live/order`는 계속 HTTP 403을 반환한다.
- 로컬에서 안전하게 완료 가능한 스키마, PostgreSQL 이관, 후보별 fail-close 점검, KIS 호가/거래제한 수집, 브로커 체결 대사 기반은 구현했다.
- 원천 미발행 또는 검증 기간이 필요한 항목은 근거 없이 통과 처리하지 않았다.

## 완료된 작업

1. `live_trading_data.py`에 후보 단위 실전 데이터 계약을 추가했다.
   - 매수는 전략 승인, 거래가능성, 당일 KIS 누적 거래대금, 15초 이내 호가, 공식 20일 거래대금, 기업행사, 희석금액을 모두 통과해야 한다.
   - 데이터가 없으면 통과하지 않고 `BLOCKED_LIVE_DATA`로 차단한다.
   - 매도는 위험 축소 목적이므로 매수 데이터 부족 때문에 막지 않는다.
2. PostgreSQL에 다음 운영 테이블을 생성했다.
   - `live_strategy_approvals`
   - `trading_restrictions`
   - `orderbook_snapshots`
   - `broker_order_reconciliation`
3. KIS 현재가 응답에서 누적 거래대금과 거래제한 원문을 저장하고, KIS 1호가/잔량을 수집하도록 했다.
4. `/api/kis-trading/live/data-preflight`를 추가했다. 주문 없이 후보의 실전 데이터 계약을 점검한다.
5. KIS 당일 체결을 `broker_order_reconciliation`에 적재하는 `scripts/reconcile_kis_live_orders.py`를 추가했다.
6. SQLite에서 PostgreSQL로 다음 테이블을 완전 이관했다.
   - `krx_security_share_snapshot`: PostgreSQL 2,840,022행
   - `security_share_history`: PostgreSQL 102,835행
   - `stock_base_info_history`: SQLite/PostgreSQL 각 8,074행
7. `scripts/verify_postgres_cutover.py` 결과는 `ok=true`, `postgres_behind=[]`이다.
8. 희석금액 적용대상 커버리지를 13,389/14,418, 92.86%로 개선했다.
9. 5% 이내 소폭 주식수 변동 2,509건을 `not_price_adjusting`으로 분리했다.
   - 희석 검토 기록은 유지한다.
   - 가격 분할/병합 보정 미확정 건수에는 포함하지 않는다.
   - 복원 파일: `research_outputs/minor_corporate_action_reclassification_backup.json`
   - 복원 명령: `python scripts/classify_minor_corporate_actions.py --restore`
10. 삼성전자 실데이터 점검에서 거래가능성, KIS 누적 거래대금, 호가, 공식 유동성, 희석 완전성이 통과함을 확인했다.

## 미완료 차단 항목

### 1. 당일 KRX 일별 파일 미발행

- 2026-08-13 KRX `stk_bydd_trd`, `ksq_bydd_trd` 응답은 각각 0건이다.
- 2026-08-12 응답은 KOSPI 942건, KOSDAQ 1,820건으로 정상이다.
- 당일 실전 후보는 KIS 누적 거래대금으로 확인하도록 구현했지만 일별 아카이브의 공식 거래대금은 KRX 파일 공개 후 재수집해야 한다.
- 재실행:
  `python -c "from scheduler import CollectionScheduler; CollectionScheduler()._job_krx_daily()"`
- 완료 조건: 최신 `price_history`에서 상장 보통주 후보의 `trade_amount>0`.

### 2. 최신 가격 미수집 57종목

- 최신 가격 커버리지는 2,636/2,693, 97.88%다.
- 거래정지, 신규상장, 코드변경, 수집 실패를 구분해야 하며 0 또는 전일가격으로 임의 대체하면 안 된다.
- 후보별 사전점검은 최신 KIS 가격이 없으면 차단한다.
- 완료 조건: 누락 종목마다 `정상 거래종목 최신가격 확보` 또는 `공식 거래불가 사유`가 기록되어야 한다.

### 3. 수급 원천 지연

- `program_stock`: 2026-08-12, 일부 종목만 확보.
- `investor_flow`: 2026-08-07.
- `short_balance`: 2026-08-12까지 원천 확보. 8월 13일 API는 데이터 없음.
- 프로그램 시장 집계는 8월 13일 KIS에서 2건 갱신했다.
- 재실행:
  `python scripts/collect_broker_program_trading.py --date YYYYMMDD --all-stocks --source both --skip-existing`
  `python collect_naver_investor.py --force --years 3`
  `python collect_short_5years.py --mode all --start YYYYMMDD --end YYYYMMDD`
- 완료 조건: 전략이 사용하는 수급 데이터셋이 모두 상태 API에서 `healthy`이고 후보 종목 데이터가 최신이어야 한다.

### 4. 기업행사 가격 보정 검토 7,058건

- 전체 9,771건 중 `factor_confirmed=204`, `not_price_adjusting=2,509`, `review_required=7,058`이다.
- 분할/병합/감자/무상증자의 DART 원문과 주식수 비율이 일치할 때만 가격 보정계수를 확정해야 한다.
- 불명확한 행을 일괄 확정하면 과거 수익률과 손절 신호가 왜곡되므로 자동 통과 금지다.
- 완료 조건: 전략 후보의 백테스트 사용구간에 `review_required`가 없고 unexplained price jump가 없어야 한다.

### 5. 희석금액 미확정 1,029건

- 적용대상 14,418건 중 13,389건이 확정됐다.
- 재실행: `python scripts/backfill_dilution_issue_amounts.py --source all`
- 완료 조건: 최근 1년 후보의 CB/BW/EB/유상증자에 `issue_amount` 누락이 없어야 한다.
- 과거 전체 100%가 아니어도 후보별 최근 1년 계약을 통과하면 실전 후보 점검은 가능하다.

### 6. 전략 성과 증거 부족

- 전략 25개 상태: `execution_strict=7`, `point_in_time_verified=1`, `point_in_time_approx=17`, `forward_validated=0`.
- `live_strategy_approvals`는 의도적으로 비어 있다.
- 전략 승인에는 최소 6개 구간 위험지표, 완전한 PIT 검증, 독립 전진검증, 증거 URI와 만료일이 필요하다.
- 완료 조건을 충족한 전략만 `approval_status='approved'`, `verification_status='forward_validated'`로 등록한다.
- 성과가 좋다는 이유만으로 수동 등록하면 안 된다.

## 검증 명령

```bash
python -m unittest -v tests.test_live_trading_data
python scripts/test_strategy_governance_live_gate.py
python scripts/verify_postgres_cutover.py
python scripts/audit_strategy_center_live_data_readiness.py
python scripts/refresh_live_execution_data.py 005930
python scripts/reconcile_kis_live_orders.py
```

## 실전 활성화 금지 조건

다음 조건 중 하나라도 남으면 `KIS_LIVE_ORDER_ENABLE=true` 또는 실전 주문 엔드포인트 구현을 진행하지 않는다.

- 준비도 감사 결과가 `BLOCKED`
- 전략이 `live_strategy_approvals`에서 유효한 전진검증 승인을 받지 못함
- 후보별 `/live/data-preflight`가 `allowed=false`
- 브로커 주문번호와 로컬 주문 생애주기를 대사할 수 없음
- 체결 대사 미해결 건이 존재함

## 2026-08-13 23:12 추가 검증 및 수정

이 절은 위의 오래된 수치보다 우선한다.

1. 최신 가격은 거래가능 후보 기준 2,668/2,668(100%)다. 가격을 확보하지 못한 25종목은
   거래정지·장기 미거래·KIS 조회불가 상태를 `trading_restrictions`에 기록해 fail-closed 처리했다.
2. 활성 전략센터 후보 30종목을 KIS로 다시 수집해 기관/외국인 수급 51행을 보완했다.
   완료된 최근 거래일(2026-08-12)은 30/30 종목의 순매수 금액이 존재한다.
3. KRX 최근 60일 강제 보완으로 공식 거래대금 4,869행을 추가했다. 최신 4개 행은 KRX
   일별 파일 미발행 구간이어서 활성 후보별 최근 20행 중 공식 거래대금은 16일이며,
   실전 데이터 계약의 최소 15일 조건은 30/30이 충족한다.
4. `collect_kis_supply_history.py`, `collect_krx_history.py`, `collect_naver_investor.py`,
   `collectors/earnings_signal_detector.py`, `scheduler.py`에서 PostgreSQL에 없는
   `SELECT changes()`와 직접 SQLite 연결을 제거했다. KRX 수집기의 PostgreSQL 비호환
   `HAVING cnt`도 `HAVING COUNT(*)`로 수정했다.
5. 선택 전략의 `trades_json`은 실제로 전부 존재했다. 기존 감사기가 `code/action` 이벤트형만
   읽어 24개 전략을 원장 없음으로 오판했다. `stock_code + entry_date/exit_date`,
   `code + buy_date/sell_date`, `sc + entry/exit` 형식을 정규화하도록 수정하고 단위 테스트를 추가했다.
6. 수정된 가격 무결성 감사 결과는 25개 전략 모두 원장을 읽었고 `passed=8`, `failed=17`,
   `no_trade_evidence=0`이다. 통과 전략은 `sector_focus`, `contract_momentum`, `golden_cross`,
   `v10`, `v2`, `earnings_conviction`, `v8`, `low_base_breakout`이다. 이 통과는 가격 오염이
   없다는 뜻일 뿐 전진검증 승인을 의미하지 않는다.
7. 실패 전략에 영향을 주는 가격점프 종목 25개를 네이버 전체 일봉과 PostgreSQL에서 교차검증했다.
   303개 이벤트, 요청 오류 0건이며 `naver_confirms_price_history=3`,
   `naver_confirms_public_raw=9`, `three_way_disagreement=288`, `external_missing=3`이다.
   상충 288건은 임의 승인하지 않았고 해당 전략은 계속 차단한다.
8. 전진검증 추적기는 PostgreSQL 기준으로 고쳤고 활성 8개 실행전략의 신호 36건과
   1/5/20/60/120/252일 결과 슬롯 216건을 동결했다. 미래 거래일이 지나야 결과를 채울 수 있다.
9. 삼성전자 후보 실데이터 점검에서는 전략 승인 외 모든 항목이 통과했다.
   KIS 누적 거래대금 9.52조원, 호가 스프레드 0.187%, 공식 거래대금 16일,
   기업행사·희석 누락 0건이었다. 따라서 현재 핵심 차단은 주문 인프라가 아니라
   `forward_validated` 전략 승인 부재다.

### 최신 재현 명령

```bash
python collect_kis_supply_history.py --codes 005930,000660 --sleep 1.05
python collect_krx_history.py --mode recent --force
python scripts/verify_price_history_with_naver.py --codes 005930,000660
python scripts/audit_selected_strategy_price_integrity.py
python scripts/audit_strategy_center_live_data_readiness.py
python -m unittest -v tests.test_live_trading_data tests.test_selected_strategy_price_integrity
python scripts/test_strategy_governance_live_gate.py
python scripts/verify_postgres_cutover.py
```

### 복구 및 되돌리기

- 가격점프 외부검증 분류는 `python scripts/audit_price_jumps_and_build_canonical.py`를 다시 실행하면
  원래 내부·공공원천 기준 감사 결과로 재생성된다.
- KRX/KIS 보완은 기존 값이 비어 있는 행만 채우며 기존 정상 시세를 덮어쓰지 않는다.
- PostgreSQL 전체 복구본은
  `/Volumes/Realtek_NVME/stock_dashboard/postgres_backups/stock_dashboard_full_20260810_125134.dump`이고,
  SHA-256과 실제 복원시험 결과는 `scripts/verify_postgres_cutover.py` 출력에 기록돼 있다.
- 소폭 기업행사 재분류는 `python scripts/classify_minor_corporate_actions.py --restore`로 복원한다.

## 2026-08-14 실행 안전성 보강

이 절은 위의 전략센터 실행 관련 수치보다 우선한다. 실전 자동매매는 계속 비활성화 상태다.

1. 가격 무결성 실패 17개 전략의 선택 run 구성요소 102건에 `price_integrity=false` 아티팩트를
   등록했다. `run_registry.derive_status()`와 `/api/backtest/matrix`가 이를 실행 게이트로 사용한다.
   재시작 후 `include_legacy=false` 매트릭스에는 통과 전략 8개만 남는 것을 확인했다.
2. 전략센터 가상매매의 모든 매수 경로(수동, golden cross, contract momentum, recovery,
   combo)가 동일한 `evaluate_risk_gates(..., strict_for_execution=True)`를 호출한다. 연결 경로는
   5개이고 배포 후 실제 후보 판정 기록은 아직 0건이다. 0건은 미연결이 아니라 실행 미관측이다.
3. `virtual_cash_accounts`, `virtual_cash_ledger`, `virtual_position_costs`를 추가해 매수·매도
   수수료, 매도세, 슬리피지, 비용 차감 순실현손익과 잔액을 원장화했다. 핵심 8개 전략 계정은
   모두 재구축됐다. `v_gc` 비용 406,470.755원, 순실현손익 -15,718,115원이며,
   `v_contract_momentum` 비용 82,424.1305원, 순실현손익 -963,815.8원이다.
4. 과거 거래 중 `gpt_v18`, `peak`는 선행 매수 없는 매도가 있고 `ai_combo`, `value`,
   `momentum`은 현금이 음수가 되어 `historical_invalid`로 격리했다. 임의 자금 추가나 거래 삭제로
   숫자를 미화하지 않았다. 상세 결과는
   `research_outputs/virtual_cash_ledger_migration_latest.json`에 있다.
5. 전진검증은 가격 무결성 통과 실행전략인 `v_gc`, `v_contract_momentum`만 추적한다. 같은 종목의
   같은 진입일을 하나의 에피소드로 축약하고, 다음 거래일 시가 진입·20거래일 결과·최소 30표본·
   최소 90일·Wilson 95% 신뢰구간을 적용한다. 현재 완료 0건, 대기 8건/9건이므로 둘 다
   `collecting`이며 미래 시간을 과거 데이터로 대체하지 않는다.
6. 종합 감사 결론은 계속 `BLOCKED`다. 원장과 게이트 연결 문제는 해결됐지만 실측 승률과
   전진검증 증거가 부족하다. 특히 기존 가상운용 실현 기준 golden cross와 contract momentum은
   모두 승률 0%라서 현재 실전 전환은 정당화되지 않는다.

### 재현 및 검증

```bash
python scripts/invalidate_contaminated_selected_runs.py
python scripts/backfill_virtual_cash_ledger.py
python scripts/capture_strategy_center_forward_signals.py
python scripts/update_live_signal_outcomes.py
python scripts/audit_forward_validation.py
python scripts/audit_strategy_center_execution_readiness.py
python -m unittest tests.test_virtual_trading_ledger tests.test_strategy_risk_gate_connection \
  tests.test_live_trading_data tests.test_selected_strategy_price_integrity
```

### 복원

- 가격오염 실행 차단 아티팩트는 `python scripts/invalidate_contaminated_selected_runs.py --restore`로
  작업 전 상태를 복원한다. 백업은
  `research_outputs/price_integrity_artifact_backup_20260814.json`이다.
- 비용 원장은 `peak_trade`를 수정하지 않는 파생 원장이다. 재생성은
  `python scripts/backfill_virtual_cash_ledger.py`로 수행하며 원천 거래는 그대로 보존된다.

## 2026-08-14 가격오염 근본원인 복구

1. 선택 레지스트리 원본에는 화면에서 보이던 25개가 아니라 `high_profit_compound`를 포함한
   26개 전략이 있다. 전체 14,731개 보유구간 중 18개 전략의 94개 참조가 68개 극단 가격
   이벤트를 통과했다. 전략 로직 자체가 18개 모두 잘못됐다는 뜻은 아니다.
2. 근본원인은 `price_history`에 KIS 수정주가, Yahoo `auto_adjust=True`, 네이버 조정계열,
   KRX 비수정 원주가를 출처·가격기준 없이 이어 붙인 것이다. 2022-01-03/04 및
   2022-05-09/10에 여러 종목의 배율이 동시에 바뀐 배치 경계가 이를 입증했다.
3. `scripts/repair_selected_price_basis.py`로 영향 26종목 전체 일봉을 단일 Naver fchart
   스냅샷에 적재했다. 25종목은 전체 기간 내부 불연속 0건으로 검증 후 적용했고,
   `032980`은 2026년 상장폐지 구간의 불연속을 제외해 선택 전략 보유 종료일인
   2022-01-17까지만 적용했다.
4. 복구 후 전체 선택 전략 재감사는 26/26 통과, 가격오염 참조 0건이다. 기존 백테스트 숫자는
   옛 가격으로 계산됐으므로 즉시 차단 해제하지 않고 `scripts/rerun_selected_after_price_repair.py`
   가 저장 파라미터 그대로 156개 run을 재계산한다. 새 6기간 모두 가격무결성 통과한 전략만
   새 suite로 원자적으로 교체한다.
5. 위험게이트는 전략별 5개 연결 방식에서 `authorize_strategy_order` 단일 관문으로 변경했다.
   가상매매, KIS 종이주문, StockEasy 실전 주문이 모두 같은 fail-closed 관문을 사용한다.
   현재 선택 전략 26개와 향후 추가 전략도 실제 주문 어댑터가 이 관문을 우회할 수 없도록
   회귀 테스트를 추가했다. 실행 어댑터가 없는 연구전략은 주문 자체가 없으므로 연결할 경로도 없다.
6. 전체 시장에는 선택 전략과 무관한 극단점프 감사행이 4,902건 남아 있다. 새 재실행이 이 중
   하나를 실제 보유구간에 포함하면 해당 새 suite는 자동 선택되지 않는다. 따라서 선택 전략의
   원천 복구와 전 시장 가격계열 정규화는 구분해서 계속 진행해야 한다.

### 가격 복구 배치와 복원

- 전체 25종목 적용 배치: `20260814T210058`
- `032980` 제한범위 적용 배치: `20260814T210507`
- `032980` 2023 재실행 보유기간 추가 적용 배치: `20260814T211731`
- `032980` 전체 표준 백테스트 종료일까지 적용 배치: `20260814T213254`
- 복원: `python scripts/repair_selected_price_basis.py --restore BATCH_ID`
- 백업 테이블: `selected_price_repair_backup`
- 신규 삽입행 추적: `selected_price_repair_inserted`
- 원천 스냅샷: `selected_price_repair_stage`

### 재실행 운영 상태

- `scripts/rerun_selected_after_price_repair.py`는 26개 전략·156개 표준기간을 지원한다.
- PostgreSQL 비호환 `HAVING cnt` 9곳을 `HAVING COUNT(*)`로 수정했다.
- 외부 `run_id` 사용 시 선행 `backtest_runs` 행이 필요한 전략을 위해 재실행기가 공통 실행행을
  먼저 생성한다.
- 거래 0건은 가격오염이 아니라 `no_trade_evidence`로 기록한다. 성과 표본 게이트는 별도다.
- 병렬 2개 실행 중 Docker Desktop이 종료된 뒤 동시 실행을 1개로 낮췄다. PostgreSQL 컨테이너
  `stock_dashboard_postgres` healthy, API 원장 응답 정상, 완료 suite는 모두 보존됨을 확인했다.

## 2026-08-14 PostgreSQL 전체 데이터 전수 감사 및 복구

이 절은 PostgreSQL `public` 전체 252개 테이블, 56,302,515행을 실제 `COUNT(*)`로 검사한 결과다.
기존 SQLite 전용 `scripts/ops/data_integrity_check.py`와 페이지 감사기는 PostgreSQL 운영 원장을
전수 검사하지 못했으므로 `scripts/audit_and_repair_postgres_data.py`를 새 기준 도구로 추가했다.

### 확정 복구

1. 가격/시세: Naver 동일 일자 검증값 9행, 잘못된 timestamp 날짜 중복 7행,
   `stock_price_daily` 동일 일자 정상 가격 복구 1,485행을 적용했다. 조정가격 반올림으로 종가가
   고가/저가 경계를 2% 이내 벗어난 38,032행은 원본을 백업하고 high/low 경계만 정규화했다.
2. 현금흐름: 내용이 완전히 같은 중복 148행을 제거했다.
3. 재무상태표: 같은 회사·연도·재무구분의 정상 연간행들이 서로 5% 이내 일치한 534행을
   교체했다. `부채총계=자산총계` 오매칭 743행과 `자본총계=자산총계` 오매칭 4행은 각각
   회계 항등식으로 원래 필드를 복구했다.
4. 희석: 공시일 기준 `security_share_history`로 분모 36행(23+13)을 복구하고 희석률을
   재계산했다. 발행금액/발행가로 확정 가능한 발행주식수 오류 29행(26+3)도 복구했다.
5. 재발방지: FnGuide/DART 파서에서 `자본과부채총계`를 부채총계로 매칭하지 않도록 했고,
   `data_write_gate.gate_financial_row()`는 오매칭 패턴이 명확할 때만 해당 필드를 고친다.
   원인이 애매하면 `BS_IDENTITY_AMBIGUOUS`로 거부하며 임의로 자본을 덮어쓰지 않는다.

### 남은 격리/검토 대상

- 가격 OHLC 320행: 308행은 `152550`의 2015~2018 조정계열이며 나머지는 비상장/매크로 또는
  소수 우선주 코드다. 모두 10% 이내 경계 차이이고 운영 보통주 universe에 속하지 않는다.
- 극단 가격점프 3,949건: `unresolved_active_common` 3,833건과
  `mixed_basis_or_price_corruption` 116건이다. 전부 `price_jump_audit.return_usable=0`으로
  수익률 계산에서 제외되어 있다. 기업행사 근거 없이 가격을 추정 보정하지 않는다.
- BS 항등식 43행: 동일 기간 정상 동료행과 source snapshot이 없어 자동 추정하지 않는다.
- 연간 매출 10배 변동 79건: 합병·신규상장·사업구조 변화가 섞여 있어 이상치 보고만 유지한다.
- 희석 분모/발행주식수 13행: 실제 대규모 증자 가능 행과 `issue_amount=30` 파서 잔존 행이
  섞여 있어 원문 재파싱 전에는 자동 보정하지 않는다.

### 복구 배치와 명령

- `20260814T214756`: 백업 1,649행
- `20260814T223802`: 백업 38,566행
- `20260814T224508`: 백업 795행
- `20260814T225056`: 백업 16행
- 복원: `python scripts/audit_and_repair_postgres_data.py --restore BATCH_ID`
- 백업 원장: `postgres_data_repair_backup`, 배치 원장: `postgres_data_repair_batches`
- 최신 증거: `research_outputs/postgres_data_quality/latest.json`
- 복원검증: `20260814T225056`의 16행을 트랜잭션 내 복원한 뒤 롤백했고 상태가 `applied`로
  유지되는 것을 확인했다.
- 회귀검증: 내장 unittest 전체 32건 통과. 운영 API는 `PAPER`, `live_order_enabled=false`다.
