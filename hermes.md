# Stock Data Integrity / 다중 AI 작업 현황 (hermes.md)

> 🪙 **토큰 최적화(2026-09-24)**: 이 파일은 Claude/Codex/code-doer가 작업 결과를 덧붙이는 로그다. **최근(2026-09-23~) 섹션만 유지**하고, 그 이전(2026-09-20~22, 낡은 '남은 작업' 수치 포함)은 [docs/hermes_archive_20260920-22.md](docs/hermes_archive_20260920-22.md)로 이관했다.
> 새 작업은 맨 아래에 `## 제목 (작성자, 날짜)`로 **10~20줄 이내** 요약만 추가하고 상세 근거는 `docs/`에 별도 파일로 둘 것. 2주 지난 섹션은 아카이브로 이동.
> **최신 사실 확인 순서**: 이 파일의 '가장 최근 섹션' → CLAUDE.md 섹션 9(알려진 이슈) → 실제 DB 조회. 과거 섹션의 건수/상태는 그 시점 값이라 현재와 다를 수 있다.

### 이관된 이전 섹션 목차 (아카이브 참조)
- Completed In This Remediation
- Remaining Work: Do Not Guess Values
- Collection Backfill Check
- Business-Report XML Fallback Implemented
- Live Price Recheck
- Historical Filing-Gap Recheck
- Evidence And Resume Points
- Stage 1 baseline mapping (code-doer, 2026-09-20)
- QUARTERLY_4WAY 잔여 출처 0건 — balance-sheet 필드복원 (code-doer, 2026-09-20)
- 손익계산서 당분기(standalone) 파이프라인 — TDD 명세+RED (code-doer, 2026-09-20)
- 손익계산서 당분기 파이프라인 — GREEN 완료 (code-doer, 2026-09-20)
- 가격 데이터 무결성 — coverage_gap 근본원인 + 정책 변경 (Claude, 2026-09-20)
- 2026-09-20 22:39:46 — P0: price_history 데이터 무결성 가드 (code-doer)
- Minervini 전략센터 등록 이어서 진행 + pykrx/FinanceDataReader로 남은 검증항목 확인 (Claude, 2026-09-20)
- invalid_ohlcv 38건 중 27건 완결 (17 백필 + 10 재조사) — 위 판정 정정 (Claude, 2026-09-20)
- 2026-09-20 — code-doer: PG 전환 승인조건 검증 산출물 (Checker 재검증용)
- 2026-09-20 — Planner priority and mandatory acceptance gates
- P0 REFACTOR 완결 — migration + financial_fix_log + source_count 연동 (code-doer, 2026-09-20
- P1 시작 — live fail-closed read-gate 명세+RED (code-doer, 2026-09-20)
- F01~F09 수정본 ↔ 런타임 경로 독립 확인 (code-doer, 2026-09-20)
- [2026-09-20 code-doer] F01~F09 커밋 후보 + 규격문서 정본 colocation
- Minervini 가상매매 자동연결 완료 + 전략센터 governance 시스템 전역 버그 발견·수정 + marcap 신규 도구 추가 (Claude, 202
- data_availability 8개 전략 실사 완료 (Claude, 2026-09-22)
- survivorship_integrity 조사 → 공용 엔진의 "부도 가정" 버그 발견·수정 (Claude, 2026-09-22)
- PIT 프로비넌스 인프라 — 설계만 완료, 구현은 보류 (Claude, 2026-09-22)
- 다중 AI 작업 재검증 및 추가 수정 (Codex, 2026-09-22)
- golden_cross 룩어헤드 편향 버그 발견·수정·재실행 완료 (Claude, 2026-09-22)

## Codex 재개 점검 (2026-09-23)

- 전날 중단 지점 이후 전체 수정 파일을 다시 컴파일했고, 청산·가격무결성·정적계약·DB호환
  테스트 **47개와 전략별 27개 subtest가 모두 통과**했다.
- 독립 전략 파일에 과거 마지막 종가/진입가를 기간말 청산가로 되살리는 패턴이 남지 않았고,
  27개 엔진 모두 `_final_liquidation_quote_for_code()`를 통해 확인된 합병·교환 회수가치를
  조회하는 것을 정적 검사로 재확인했다.
- v4 21.12~22.10의 0거래 원인을 수정 전 suite와 대조했다. 두 suite 모두 KOSPI MA120
  시장필터가 225/225일 신규매수를 차단해 0거래였으므로, 이번 후보 종목 제외 변경이 만든
  회귀가 아니다. 거래 증거가 없는 컴포넌트를 fail-close로 유지한다.
- 최종 선택 v4 suite는 `4807bc8857ea6119`, 동적 검증 상태는 계속 `legacy`다. 청산 버그와
  survivorship finding은 해소됐지만 `universe_integrity`와 `data_availability`가 남아 있으므로
  성과 채택 대상으로 승격하지 않는다.

### 남은 5개 지연 재무전략의 룩어헤드 정적 점검

최신 golden_cross 수정 기록에서 요구한 v8/v4/recovery/earnings_supply_discovery/
high_profit_compound도 실제 소비 지점까지 확인했다.

- **v4**: `fin_disclosure_dates.avail_date`를 우선하고 법정기한 fallback을 `fin_all`에 저장한
  뒤, 공용 `_get_financial_as_of(..., decision_date)`가 `release <= target_date`인 행만 고른다.
- **v8**: v4와 같은 avail_date 구성 후 매수 판단일 `d`로 `_get_financial_as_of(fins, d)`를
  호출한다.
- **recovery**: `(avail_date, net_income, revenue, year, quarter)`로 적재하고 모든 흑자전환/
  재무건전성 판단에서 `avail_date <= day`인 행만 사용한다.
- **earnings_supply_discovery**: `_release_date()`로 종목별 실제 DART 공시일을 우선 계산하고,
  `_current_growth()`가 `avail_date <= day`인 최신 이벤트만 사용한다.
- **high_profit_compound**: 후속 Claude 수정으로 `dart_contracts.disclosed_at`과 order_backlog의
  분기별 법정 공시 가능일을 이벤트로 만들고, 매수일 현재 `avail <= as_of`인 촉매만 사용한다.

따라서 이 다섯 전략에서 golden_cross와 같은 “미래 연도 재무값 직접 사용” 패턴은 현재
코드에 남아 있지 않다. 이 판정은 계산 로직의 정적 검증이며, `data_availability` 게이트가
요구하는 신호별 소비 행 ID/available_at 영속화가 완료됐다는 뜻은 아니다. PIT provenance
인프라는 계속 미완료로 유지한다.

## 5개 전략 룩어헤드 전수점검 완료 — high_profit_compound에서 2번째(더 심각한) 버그 발견·수정 (Claude, 2026-09-22)

소유자 질문 "추가 개선할건 없어?"에 답하며 위에서 남긴 숙제(나머지 5개 전략 룩어헤드
전수점검)를 바로 실행.

**점검 결과**:
- **v8** (`sd['fins']` → `_get_financial_as_of()`): 안전. 공용 PIT 헬퍼가 `avail_date<=
  target_date`를 정확히 필터링.
- **v4**(=base.py, `_is_buy_signal`의 Graham 가치스크리너): 안전. 동일하게
  `_get_financial_as_of()` 사용, 주석에도 "Graham 공시일 지연 적용" 명시.
- **recovery**(`_fin_healthy`/`_is_turnaround`/`_is_turnaround_rev_growth`): 안전.
  자체 인라인 로직이지만 `avail = [x for x in fl if x[0] <= day]`로 정확히 필터링.
- **earnings_supply_discovery**(`_current_growth`): 안전. `_release_date()` 기반
  avail_date 계산 후 `avail = [e for e in evs if e[0] <= day]`로 정확히 필터링(주석에
  "Codex PIT 연구와 동일 정의"라고 명시돼 있어 이미 한 번 검증된 로직으로 보임).
- **high_profit_compound**: **2번째 룩어헤드 버그 발견**(golden_cross보다 영향범위 큼).

**high_profit_compound 버그**: `catalyst_codes = contract_codes | backlog_codes`가
`dart_contracts`/`order_backlog`를 **날짜 필터 전혀 없이** "연도 변동이 적으므로"라는
검증 안 된 주석 하나로 전체 이력을 한 번에 로드했다. `catalyst_codes`는 `buy_universe`의
**하드 AND 조건**(진입 우선순위 보너스가 아니라 아예 매수 후보에서 배제하는 게이트)이라
golden_cross(랭킹 보너스 하나)보다 구조적으로 더 심각.

**실측 확인**: `dart_contracts`(signal_strength≥2)는 2021-05-31~2026-09-22에 846개
종목이 분산 공시돼 있는데, `as_of=2021-06-30`이면 실제로는 0개만 공시됐어야 하나 구버전
코드는 846개 전부를 인식. `order_backlog`는 별도 공시일 컬럼이 없어(collected_at은
수집시각일 뿐) 다른 전략과 동일한 법정기한 근사(분기+45일/연간 익년 3월31일)로 계산.
전체(미필터) distinct 코드 1,375개 대비, 필터링 후 as_of=2021-06-30→437개,
2022-06-30→630개, 2024-06-30→1,071개, 2026-06-30→1,321개로 시간에 따라 정확히
단조증가 — 버그가 실재했고 수정이 올바르게 작동함을 확인.

**수정**: `_insider_buy_codes(as_of)`와 동일한 패턴(7일마다 갱신되는 캐시)으로
`_catalyst_codes_as_of(as_of)` 신규 작성 — dart_contracts는 실제 `disclosed_at` 컬럼
그대로 사용, order_backlog는 (year,quarter)→법정기한 근사로 avail_date 계산 후 둘 다
`avail<=as_of` 필터링. 정적 `catalyst_codes` 집합을 매일 갱신되는 `_catalyst_cache`로
교체. 전체 회귀 231건 통과 확인 후 6기간 재실행.

**재실행 결과**: avg6 ≈ +24.8%(98.51/-38.95/42.52/12.89/11.29/22.54, 수정 전 22.79%와
비슷한 수준이지만 기간별 거래수가 22/21/13/19/16/13으로 실제 매수 유니버스가 달라졌음을
확인 — 순수익률은 비슷해 보여도 "어떤 종목을 샀는지"는 실제로 바뀜). price_integrity는
6기간 **합산** 오염률(2.88%)은 통과지만, governance가 실제로 보는 **기간별 개별** 기준으론
20.3~21.11 한 구간만 자체 임계값(7%)을 초과해 실패 — `externally_confirmed_internal_
corruption`/`externally_confirmed_price_jump_review` 오염(005440/099320 등), golden_cross의
21.12~22.10과 똑같이 이번 룩어헤드 수정과 무관한 사전 존재 `unresolved_active_common`류
문제. survivorship_integrity는 6기간 전부 0건 통과.

**별개로 발견한, 이번엔 안 고친 문제**: high_profit_compound는 `execution_contract`/
`cash_reconciliation`/`next_open` 아티팩트조차 등록되지 않는다 — 이건 이 전략이 공용
엔진(`_run_generic_backtest`/`_run_portfolio`)이 자동 등록해주는 경로를 안 타는 자체
루프(bespoke loop)라서 생기는, 오늘 수정한 룩어헤드 버그와는 무관한 더 근본적인 배선
누락. golden_cross 등 다른 bespoke 전략들은(Codex 세션이 `_final_liquidation_quote_for_code`
연결한 것과 별개로) 이 문제가 없었는지 다음 세션에 추가 확인 필요.

**부수 수정**: `tests/test_price_integrity.py`의 SQLite 테스트픽스처 `corporate_action_events`
테이블에 `backward_price_factor` 컬럼이 없어서 `test_expanded_audit_and_raw_agreement_
never_approve` 1건이 실패 중이었음(동시세션이 `scripts/audit_price_jumps_and_build_
canonical.py`를 그 컬럼 참조하도록 고치면서 테스트픽스처는 안 맞춤) — 컬럼 하나 추가로
해소, 프로덕션 로직 변경 없음.

**최종 governance 상태**: suite `1ed4d7b48b069a8f` 등록, 여전히 `legacy`(reasons:
`execution_contract`/`cash_reconciliation`/`next_open`/`data_availability`/
`point_in_time_exact`/`forward_validation` — 전부 사전 존재 배선/인프라 미비이지 오늘
수정과 무관, 20.3~21.11 구간만 `price_integrity`/`corporate_action_integrity`도 실패).
golden_cross와 마찬가지로 **버그 자체는 확실히 제거됐지만 governance 등급 승격은
별개의, 더 큰 미해결 과제들(PIT 인프라, unresolved_active_common 풀, bespoke 루프의
아티팩트 자동등록 배선)에 달려 있다** — 이 셋 중 어느 것도 오늘 세션 범위가 아니었으므로
legacy 유지는 "실패"가 아니라 정확한 현재 상태 반영.

**최종 결론**: 이번 세션에서 발견한 룩어헤드 버그는 총 2건(golden_cross, high_profit_compound)
— 둘 다 실측으로 확정하고 실제 수정+재실행까지 완료. 나머지 4개(v8/v4/recovery/
earnings_supply_discovery)는 이미 안전했음을 코드 직접 확인으로 검증(추정 아님). 전체
231개(부수 수정 후) 테스트 통과.

---

## 남은 작업 이어서 진행 (Claude, 2026-09-23) — 282690/004200 해소, high_profit_compound 3번째 결함, PIT 인프라는 동시세션에 양보

소유자 지시 "남은거 진행해"로 이전에 남겨둔 4개 항목을 순서대로 처리.

### 1. high_profit_compound 배선 점검 → 3번째 결함 발견·수정

다른 25개 전략에 execution_contract/cash_reconciliation 미등록 문제가 더 있는지 전수
조회 — **high_profit_compound 1개만** 해당(다른 전략은 전부 정상). 원인 확인: 이 전략만
`_register_execution_artifacts()`를 아예 호출하지 않음(2026-07-14에 sector/recovery/
turnaround/v8/v12/golden_cross 6개는 이관됐는데 이 파일만 빠짐). 추가로 **3번째, 더
근본적인 결함**도 발견: 다른 26개 전략은 동시세션이 안전한 `_final_liquidation_quote_
for_code()`로 이관했는데 이 파일만 자체 헬퍼 `_close_as_of`(date<=? ORDER BY date DESC —
상장폐지 후에도 몇 달 전 마지막 종가를 조용히 재사용, 그마저 없으면 포지션 자체가
trades에서 통째로 누락)를 그대로 썼다.

**수정**: (1) `_register_execution_artifacts(rid, capital, capital+total_pnl)` 호출 추가
— `cash` 변수가 기간종료 강제청산분을 반영 안 해서 `capital+total_pnl`로 직접 계산.
(2) 기간종료 처리 루프를 종목별 실제 시세이력 기반 `_final_liquidation_quote_for_code()`
호출로 교체 — 포지션이 조용히 사라지지 않고 실제종가/확인된 승계가치/명시적 전액손실
중 하나로 항상 명확히 처리됨. 단건 검증(2024-11~2025-05): execution_contract=True,
cash_reconciliation=True로 정상 등록 확인. 6기간 전체 재실행 진행 중(다음 섹션에서
결과 정리 예정).

### 2. 282690(동아타이어) 승계가치 완전 해소

승계종목명이 "디엔오토모티브"(한글)라 DART corp_codes 검색에서 계속 실패했었는데,
실제 등록명은 **"DN오토모티브"(영문 DN)** — corp_code 검색에서 "오토모티브"로 넓혀
찾음: **종목코드 007340**. marcap으로 정산일(2024-10-08) 확인: 발행주식수가
999만4천→6,056만(6.06배)으로 실제 급증 — 진짜 합병신주 발행 확인. 우리 DB의 007340
종가(2024-10-08 19,200원)가 marcap과 정확히 일치(pykrx만 이번에도 매끈하게 조정된
다른 값을 보여줌 — 이번 세션 3번째 pykrx 조정 아티팩트 사례, 000060·016385에 이어).

`delisting_outcomes`에 등록: 합병비율 1주당 0.1558169주 × 007340 종가 19,200원 =
**주당 2,991.68원** 회수가치(교환 직전 정지가 13,500원 대비 실질 약 -78% 손실이지만,
기존 엔진의 -100% 가정과는 다름 — "부도가 아니라 실제로도 큰 손실이었다"는 것도
근거 있는 결론이면 그대로 반영).

### 3. 004200(고려개발) — "미확정"이 아니라 "시장가 계산 불가능"으로 확정 결론

주식회사 삼호(존속회사)의 DART 기업정보 직접 조회: `corp_cls='E'`(비상장), `stock_code`
공란, 부산 소재, 2017~2024년까지 매년 감사보고서만 제출(주권상장법인 공시 없음).
즉 고려개발 주주들은 **애초에 시장가격이 존재한 적 없는 비상장주식**을 받았다 —
이건 "증거를 더 찾아야 하는 미해결"이 아니라 "시장 데이터로는 원천적으로 계산 불가능"
이라는 확정 결론. `delisting_outcomes`에 추가하지 않고 기존 zero_recovery 기본값을
그대로 유지하는 게 맞다(억지로 숫자를 만들지 않는다는 원칙).

### 4. PIT 인프라 — 동시세션이 더 완성도 높은 버전을 이미 구축 중, 제 작업은 철회

golden_cross에 PIT 프로비넌스 계측(신규 테이블 `signal_data_provenance` + 공용 헬퍼
`_record_financial_provenance`)을 배선하던 중, **동시세션(Codex로 추정)이 정확히 같은
문제를 이미 더 발전된 형태로 풀고 있음을 발견**:
- `_run_portfolio()`에 `financial_provenance: list`(출력 파라미터, 호출자가 넘기면
  엔진이 채움) + `entry_blocked_dates`(관련 기능) 파라미터가 이미 추가돼 있었고,
  `_register_financial_provenance_artifact(run_id, records, trade_count)`라는 별도
  공용 라이터와 신규 테스트(`test_temporal_price_mask_and_financial_provenance_
  follow_actual_entry`)까지 이미 존재.
- 제가 만든 테이블(`signal_data_provenance`, 컬럼: run_hash/stock_code/year/quarter/
  is_annual/avail_date_used/first_used_date)과 이름이 같은데 스키마가 다름(저쪽은
  run_id/entry_date/dataset/source_row_id/source_key 등). 실제 라이브 스키마를
  확인해보니 제 것도 저쪽 것도 아닌 세 번째 중간 형태였음(`decision_date`+`year`+
  `quarter`+`is_annual`+`avail_date_used` 혼재) — 여러 세션이 동시에
  `CREATE TABLE IF NOT EXISTS`를 시도하면서 먼저 실행된 것이 남고 나머지는 조용히
  무시된 것으로 보임. 다행히 데이터는 0건이라 실질적 충돌(데이터 손상)은 없었음.
- **조치**: 제 golden_cross 계측 코드(`_financial_provenance_events`,
  `_record_financial_provenance`)를 전부 되돌림 — 룩어헤드 수정(2026-09-22, 검증
  완료)은 그대로 유지. 대신 부수적으로 발견한 진짜 버그 하나는 남김:
  `_get_financial_as_of()`가 `fin_rows`에 `None` 항목이 섞이면 크래시하는 방어로직
  누락을 고쳤다(`if row is None: continue` 한 줄 추가) — 이건 동시세션의 새 코드
  (`_run_portfolio`의 신규 `fin = _get_financial_as_of(sd['fins'], day, sc)` 호출)가
  테스트 픽스처의 `fins=[None,None]`에서 실제로 크래시내던 걸 잡은 것으로, 누구의
  설계 방향과도 상충하지 않는 순수 방어적 보강.
- 전체 회귀 232건(동시세션 신규 테스트 포함) 통과 확인.

**권고**: PIT 인프라는 이제 동시세션이 v4를 대상으로 이미 진행 중인 걸 이어받는 게
맞다 — 같은 것을 두 갈래로 만들면 스키마 충돌만 더 커진다. 다음 세션은 그 세션의
결과물(`_run_portfolio`의 `financial_provenance` 파라미터 + `_register_financial_
provenance_artifact`)이 실제로 완료·안정화됐는지 먼저 확인한 뒤, golden_cross 등
`_run_portfolio`를 안 쓰는 나머지 전략들에 같은 패턴을 어떻게 확장할지 그쪽 설계를
따라가며 정하는 게 순서.

---

## v4 시점별 가격 차단·재무행 provenance 개선 (Codex, 2026-09-23)

사용자 지시로 중지 지점부터 v4의 남은 두 구조적 결함을 수정하고 운영 DB에서 6기간을
다시 실행했다.

### 수정 1: 가격 이상치가 있는 종목 전체 제외 제거

기존 v4는 워밍업~종료일 사이에 미검증 가격이 단 하루라도 있으면 그 종목을 전체 기간
유니버스에서 제거했다. 이는 미래에 발생할 이상치를 과거 진입 시점에도 알고 종목을
제외하는 선택편향이다. `price_integrity.research_price_issues()`로 행 단위 이상치를 읽고,
종목은 유니버스에 유지한 채 이상 관측일 이후 해당 값이 최대 기술지표 창에서 빠지는
252거래일 동안만 신규 진입을 차단하도록 바꿨다. `universe_integrity`에는 영향 종목,
이상 이벤트 수, 차단 stock-day 수를 남기며 `excluded_count=0`으로 기록한다.

### 수정 2: 실제 체결별 재무 데이터 provenance 저장

v4 재무 조회에 `financial_data.id`와 `report_type`을 추가했다. 신호일에 실제로 보였던
행을 pending order에 붙이고, 익일 주문이 실제 체결됐을 때만 `run_hash`, 종목코드,
신호일, 체결일, 원천 행 ID, `available_at`, 회계기간을
`backtest_signal_data_provenance`에 저장한다. 재무행이 없던 신호는 `source_row_id=NONE`으로
명시해 지연 데이터를 사용하지 않았다는 사실도 증명한다. 모든 체결이 증빙되고
`available_at <= decision_date`일 때만 `data_availability`를 통과한다.

기존에 다른 세션이 만든 `signal_data_provenance`는 컬럼이 다른 중간 스키마였기 때문에
첫 실행의 증빙 저장이 실패했다. 기존 테이블과 데이터는 변경하지 않고 전용 테이블명으로
분리했으며, 그 실행은 선택하지 않았다. 중단된 run 1건은 `error`로 정리한 뒤 전 기간을
깨끗하게 재실행했다.

### 운영 재실행 및 감사 결과

- 선택 suite: `b592897d205f3e1f`
- 기간 수익률: `+21.12 / 0.00 / +10.49 / -2.97 / -1.04 / +23.31%`
- 6기간 단순평균: `+8.48%`, 양수 3/6
- 거래 수: `288 / 0 / 168 / 91 / 70 / 189`, 합계 806건
- 재무 provenance: 거래가 있는 모든 기간에서 체결 수와 증빙 수가 정확히 일치,
  `available_at > decision_date` 0건. 0거래 기간도 지연 데이터 소비 0건으로 통과.
- 가격 감사: 보유창 806개 중 오염 1개(0.12%), 생존편향 0건, 전략 전체 통과.
- 유니버스 감사: 6기간 모두 전체 종목 제외 0건, 시점별 차단 정책으로 통과.
- 관련 회귀: 54 tests + 27 subtests 통과.

### 남은 상태

거래가 없던 `2021-12~2022-10` 기간은 KOSPI MA120 시장 필터가 전 기간 신규 매수를
차단한 기존 전략 결과다. 보유창이 0개라 가격·생존·기업행위 감사를 증명할 표본도 0개이고,
현 정책은 이 세 게이트를 fail-close한다. 따라서 나머지 5개 컴포넌트는
`point_in_time_approx`까지 승격됐지만 suite 표시는 `legacy`다. 또한 전 기간에
security master/share history의 근사 구간이 남아 `point_in_time_exact`는 통과하지
않는다. 이는 이번 v4 로직 결함과 별개인 데이터 이력 인프라 한계이며, 0거래 기간을
억지로 통과 처리하거나 거래를 인위적으로 만드는 변경은 하지 않았다.

(위 섹션에서 언급된 "다른 세션이 만든 signal_data_provenance"는 제 PIT 프로토타입입니다
— 스키마 충돌을 스스로 확인하고 철회한 것과 이 결과가 일치합니다. 별도 테이블명으로
분리해주셔서 제 쪽 잔여물과도 충돌 없음 확인.)

---

## high_profit_compound 3번째 수정에서 발견한 2차 버그 — end_date vs 실제 마지막 거래일 (Claude, 2026-09-23)

3번째 수정(안전한 청산 헬퍼로 교체) 적용 후 6기간 재실행한 결과, 23.11~24.12와
24.6~25.5 두 구간이 크게 바뀌었는데(24.6~25.5는 +11.29%→**-98.56%**), 실제 보유 종목을
까보니 **042660(한화에어로스페이스)/329180(HD현대중공업)/207940(삼성바이오로직스)**
같은 초대형 우량주까지 전부 "시세부재 전액손실"로 처리돼 있었다 — 명백히 진짜 상장폐지가
아니라 제가 방금 만든 수정 자체의 버그.

**원인**: `_final_liquidation_quote_for_code(conn, code, last_day, idx_map, prices)`를
호출할 때 `last_day`로 `end_date`(백테스트 파라미터, 달력상 종료일 — 예: 2025-05-31은
토요일)를 그대로 넘겼다. 실제 마지막 거래일은 2025-05-30인데, `idx_map`에 '2025-05-31'
키가 없으니 "이 날짜엔 시세가 없다"→"상장폐지"로 오판. golden_cross/v8 등 이미 검증된
전략들을 다시 확인해보니 전부 `last_day = sim_dates[-1]`(그 전략 자신의 실제 거래일
캘린더의 마지막 날)을 쓰고 있었다 — `end_date` 파라미터를 직접 쓴 건 내 실수.

**수정**: `_last_trading_day = sim_dates[-1] if sim_dates else end_date`로 교체, 가격
조회와 `_final_liquidation_quote_for_code` 호출 둘 다 여기에 맞춤(trades 기록의 표시용
`sell_date`는 기존처럼 `end_date` 유지 — 라벨일 뿐 계산에 안 쓰임). 회귀 232건 재확인
통과 후 6기간 3차 재실행 진행 중.

**교훈**: 이미 검증된 다른 전략의 정확한 호출 패턴을 문자 그대로 베꼈어야 했는데
파라미터 이름(`end_date`)만 보고 "당연히 이게 맞겠지"라고 넘겨짚은 게 원인 — 수정
직후 극단적인 결과(-98.56%, 초대형주 포함)가 나왔을 때 "진짜 버그를 찾았다"고 성급하게
결론 내리지 않고 즉시 원인을 파고든 덕에 재실행 전에 잡을 수 있었다.

**최종 결과(3차 재실행)**: 24.6~25.5가 +11.29%로 정상 복귀(버그로 인한 -98.56%가
아니었음을 재확인), 23.11~24.12는 -31.96% 그대로 유지(이건 버그가 아니라 3번째 수정
자체로 인한 진짜 변화 — 1차/2차 재실행 모두 동일값이라 확정). avg6 ≈ +17.33%(98.51/
-38.95/42.52/-31.96/11.29/22.54). `register_run_set`+`select_run` 재등록 후 감사스크립트
재실행 결과: **execution_contract/cash_reconciliation 정상 등록 확인**(reasons 목록에서
사라짐 — 오늘 수정 1번 완전히 검증됨). 새로 나타난 `next_open` 실패는 버그가 아니라
기존 코드의 2026-07-18자 주석에 이미 정직하게 문서화된 설계("정수주식이 아닌 금액모델
이라 레거시 등급으로 기록") — `execution_timing="same_close"`를 쓰는 이 엔진이 next_open
체결을 요구하는 게이트를 통과하려면 체결모델 자체를 바꿔야 하는 별도의 더 큰 작업이라
이번 범위 밖. 전체 회귀 232건 통과.

---

## unresolved_active_common pykrx 검증 — 대형 발견: 2010~2021 구간 전반의 미확인 가격기준 불일치 (Claude, 2026-09-23)

남은 4번째 항목(`unresolved_active_common` 6,352건) 착수. 기존에 이미
`scripts/verify_price_history_with_naver.py`(Naver 소스)가 활발히 운영 중임을 확인,
중복을 피하려 **pykrx를 세 번째 독립소스로 추가**하는 `scripts/verify_
unresolved_active_common_with_pykrx_20260923.py` 신규 작성(같은 `external_price_
verification` 테이블 재사용, `external_source='pykrx'`). `unresolved_active_common`은
`PROTECTED_FROM_OVERRIDE`에 없어 재분류 가능하지만, 기존 Naver 스크립트와 동일한 철학
그대로 — 외부소스 일치도 `return_usable`을 되돌리지 않고 라벨만 더 정밀하게(`externally_
confirmed_*`) 만든다(경제적 수익성 검증은 별도).

### 예상 밖 결과: 상위 300종목(전체의 69% 커버) 중 단 0건 승격

건수 많은 상위 300종목(4,398행)을 pykrx로 대조했더니 **96.4%가 "3자불일치"**로
나왔다(승격 0건). 처음엔 로직 버그를 의심했으나, 015360(INVENI) 한 건을 수기로 검증한
결과 — pykrx가 우리 이벤트종가와는 정확히 일치하는데 **우리 "전일종가" 필드만** 5배
차이. 로직은 정확했고, 발견은 진짜였다.

### 무작위 표본에서 000670(SK하이닉스) 발견 — 근본원인 조사

무작위 8건 표본에서 000670(SK하이닉스, 최상급 우량주)이 2021-02-16→17에 하루 만에
553,000→54,465(-90%)로 기록된 걸 발견 — 실제로는 불가능한 하루 등락(서킷브레이커
±30% 상한). 조사 과정:

1. **pykrx 재확인**: 2021-02-10~22 구간에서 pykrx는 50,319→52,109→54,465→...로
   완전히 매끄럽게 연속(불연속 없음).
2. **1차 결론(성급했음)**: 2021-02-17에 실제로 없는데 우리 DB에만 있는 급락 →
   "우리 백필 파이프라인 버그"로 잠정 결론, DART로 그 시점 액면분할/병합/감자 공시
   확인 → **없음**(2020-12~2021-02 구간 공시 0건) → 1차 결론 강화.
3. **035720(카카오)/256940/300720 등도 같은 서명 발견**: 특정 전환일 이전엔 종목마다
   다른 고정 배율(4.98배/3.0배/0.778배 등)이 붙어 있다가 그 날부터 pykrx와 정확히
   일치 — 단일 종목 문제가 아니라 광범위한 패턴.
4. **기존 선례 발견**: `scripts/apply_20181224_splice_repair.py`가 이미 "배치 스플라이스
   손상"(2018-12-24~28 구간 한정)을 다뤄본 적이 있고, 그 스크립트 자체가 "2x/5x/10x
   같은 깔끔한 배수에 가까운 건 진짜 미확인 분할일 수 있어 이 스크립트가 추측하면 안
   된다"고 명시적으로 제외해뒀던 카테고리와 정확히 겹침.
5. **DART 전체 이력 재확인(000670, 2010~2026 전체)**: **2025-04-10 주식분할결정
   발견** — 액면가 5,000원→500원, 1주당가액 10:1, 발행주식 184.2만→1,842만주,
   신주상장 2025-04-25. **1차 결론이 틀렸다** — 진짜 10:1 분할이 존재했다(다만
   2021-02-17이 아니라 2025-04-25).
6. **2025년 실제 분할 구간 데이터 재확인**: 2025-04-14~24(매매정지 실제 공시기간)엔
   367,500원(높은 기준)으로 얼어있다가, 04-25(신주상장일) 이후 35,847원(낮은 기준)
   으로 정확히 이어짐 — **그런데 04-11(정지 직전)도 이미 35,605원(낮은 기준)이었다**.
   즉 우리 데이터는 실제 분할(2025-04)보다 **4년 넘게 앞선 2021-02-17에 이미 낮은
   기준으로 전환**돼 있었고, 정지기간 중에만 367,500원(높은 기준)으로 잠깐 튀는
   별도의 이상현상까지 겹쳐 있다.

**현재 결론(확정 아님, 다음 세션 최우선 과제로 명확히 인계)**: pykrx는 2025년 실제
분할을 2010년까지 소급 조정해서 보여주는 것으로 보이고(이번 세션 반복 확인된 pykrx
습성과 일치), 우리 DB의 2021-02-17 이후 구간은 그 소급조정 기준과 이미 일치한다 —
그런데 **왜 우리 DB가 실제 분할일(2025-04)보다 4년 이상 앞서 그 "미래 기준"으로 이미
전환돼 있는지는 아직 설명되지 않는다**. 다음 세션이 조사할 구체적 질문:
- 2010-01-04~2021-02-16(11년, ~2,800행)이 진짜 당시 원가(raw)인가, 아니면 이 구간
  자체가 오염된 건가 — SK하이닉스의 실제 2010~2011년 사업보고서/공시 종가를 직접
  대조해야 확정 가능(이번 세션엔 시간상 못함).
- 035720/256940/300720 등 같은 서명을 보인 다른 종목들도 각자 실제 분할/병합 이력이
  있는지 DART로 개별 확인 필요(035720=카카오는 유명 대형주라 우선순위 높음).
- 2025-04-14~24 정지기간의 367,500원 이상현상(정지 직전 35,605원과 10배 차이)은
  별도의, 아직 설명 안 된 3번째 현상 — 정지 처리 로직 자체의 버그일 가능성.
- 이 서명(장기간 고정배율 후 특정일 전환, DART 공시일과 불일치)에 맞는 다른
  `unresolved_active_common` 행이 몇 개나 더 있는지 규모 파악 필요.

**아무것도 고치지 않았음** — 근본원인이 불확실한 상태에서 SK하이닉스 같은 초대형주의
11년치 가격을 성급하게 수정하는 리스크가 너무 크다고 판단. pykrx 검증 스크립트
자체는 유효하고 재사용 가능(`--limit-stocks`로 범위 조절, `--apply` 없이 dry-run
가능) — 다음 세션은 이 스크립트로 더 많은 종목을 스캔해 규모를 먼저 파악하는 것부터
시작하는 게 좋다.

---

## 000670(SK하이닉스) 미스터리 완전 해결 + 실제 수정 완료 (Claude, 2026-09-23)

소유자 지시("SK하이닉스 조사")로 위에서 "다음 세션으로 미룬" 미스터리를 바로 이어서
조사, **완전히 해결하고 실제로 수정까지 완료**.

### 미스터리 해결 과정

marcap으로 2010년 값(569,000원, 발행주식 1,842,040주)이 우리 DB와 정확히 일치함을
재확인 — 즉 **2010~2021-02-16 구간은 처음부터 옳았다**(1차 조사에서 "우리 데이터가
틀렸을 수도"라고 남겨둔 의심은 기각). 그렇다면 문제는 2021-02-17 이후 구간이어야 한다
— marcap으로 그 날짜를 직접 확인하니 **578,000원, 발행주식 1,842,040주(비분할)로
정상 연속**, 아무 이상 없음. 즉:

- **2010~2021-02-16**: 우리 DB 정상(marcap 일치)
- **2021-02-17~2025-04-24**: **우리 DB만** 분할후 기준(~10.61배 낮은 가격,
  비례해서 높은 거래량)으로 잘못 저장됨 — marcap은 이 구간 내내 정상(원가) 유지
- **2025-04-25~현재**: 진짜 분할(DART 확인: 2025-04-10 이사회결의, 액면가
  5,000→500원 10:1, 발행주식 1,842,040→18,420,400주, 신주상장 2025-04-25) 반영 후라
  정상

즉 **원인은 우리 자체 price_history가 2021-02-17부터 2025년의 미래 분할을 4년 넘게
미리 반영한 잘못된 기준으로 덮어써져 있었다**는 것 — 정확한 배후 원인(어느 백필/재적재
배치가 이랬는지)은 이번에도 못 찾았지만, 결과 자체는 marcap(원가, 2010년 값+발행주식수
교차검증)과 DART(진짜 분할일/비율 공식 확인)로 완전히 확정됐다.

### 실제 수정

`scripts/apply_000670_split_basis_splice_repair_20260923.py` 신규 작성 — marcap의
2021-02-17~2025-04-24 구간 OHLCV(1,029행, 정지기간 O=H=L=0/close유지 정지마커 포함
— 이것도 marcap 자체가 그렇게 기록하고 있어 별도 이상현상 아님을 재확인)를 그대로
가져와 `price_history`를 교체. 기존 세션 관례 그대로 `price_history_fix_backup`+
`data_fix_log`+run_id로 백업·기록. dry-run(1,015건 후보) → `--apply` 적용 완료,
2021-02-17 재조회로 578,000원/7,512주 정상 반영 확인.

`corporate_action_events`에도 이번에 확정된 진짜 분할(2025-04-25, 10:1, DART
rcept_no=20250410800652, `adjustment_status='factor_confirmed'`)을 신규 등록 —
이제 이 이벤트를 근거로 후속 전략들의 기업행위 조정 로직(`_corp_action_adjusted_entry`
류)이 이 구간을 가로지르는 보유창을 자동으로 올바르게 처리할 수 있다.

`price_integrity.rebuild_views()` 재실행 결과 000670의 `price_jump_audit` 플래그가
크게 줄었음(정확한 이전 건수는 기록 안 했으나 2021-02-17 전후 구간 다수 → 10건까지
감소). 전체 시장 대상 `scripts/audit_price_jumps_and_build_canonical.py`는 무거운
전역 재구축이라 타임아웃(동시세션도 이 영역 성능을 이미 최적화 중이었던 것과 일치) —
내 종목 1개만을 위해 무리하게 강행하지 않음, 남은 세부 라벨(2021-02-17의 낡은
`externally_confirmed_internal_corruption`, 2025-04-25의 아직 미승격된
`unresolved_active_common`)은 다음 정기 실행 때 자동으로 갱신될 것. 전체 회귀 232건
재확인 통과.

### 남은 것(축소됐지만 완전히 끝난 건 아님)

- **2022-02-22/23, 2022-03-11/17**: 000670에 남은 다른 unresolved 플래그 4건 —
  가격을 직접 대조해보니 변동폭이 작아(650,000→641,000 등) 이번 splice 버그와
  무관한 별개 사안으로 보임(시간상 추가조사 안 함).
- **다른 종목들**: 035720(카카오)/256940/300720 후속 확인 완료(아래 새 섹션 참고) —
  035720은 000670과 동일 패턴으로 확정·수정, 256940은 분할이력 없어 보류, 300720은
  더 복잡한 별개 패턴이라 보류.
- **근본원인 자체**: "어느 배치/스크립트가 이 splice를 만들었는지"는 여전히 미상 —
  같은 버그가 계속 재발하지 않으려면 이것도 찾아야 함.

---

## 035720(카카오) 동일 패턴 확인·수정 + 256940/300720 최종 판정 (Claude, 2026-09-23)

소유자 지시("계속해")로 000670과 같은 서명을 보인 나머지 종목들을 동일 방법론(marcap
원가+DART 실제분할이력 이중교차검증)으로 이어서 확인.

### 035720(카카오) — 000670과 완전히 동일한 패턴, 확정·수정 완료

DART 조회 결과 2014년 다음-카카오 합병(우회상장, 035720은 원래 다음의 코드)이 먼저
눈에 띄었으나, marcap으로 2014-12-30(123,600원)→2015-01-02(137,200원, 발행주식
57,699,951주로 양쪽 동일) 구간이 완전히 매끄럽게 연속됨을 확인 — **2014년 합병 효과는
이미 그 이전에 다 정산된 상태**였고 무관했다. 진짜 원인은 000670과 똑같이 **2021-02-25
DART 주식분할결정**(액면가 500→100원, 5:1, 88,704,620→443,523,100주, 신주상장
2021-04-15) — 우리 DB만 2015-01-02부터 6년 넘게 앞서 분할후 기준(≈4.98배 낮은 가격)
으로 잘못 저장돼 있었다.

**수정**: `scripts/apply_000670_split_basis_splice_repair_20260923.py`를 종목 일반화한
`scripts/apply_split_basis_splice_repair.py`(신규, `--stock-code`/`--marcap-parquet`/
`--reason` 인자화) 작성. 2015-01-02~2021-04-11 구간(halt 시작 전날까지 — halt기간
2021-04-12~14는 우리 DB에 이미 원가 정지마커로 정상 존재) marcap 원가 1,536행 후보 →
적용 완료. 2015-01-02 재조회로 137,200원/발행주식 반영 정상 확인.
`corporate_action_events`에 2021-04-15 이벤트 신규 등록(`factor_confirmed`, DART
rcept_no=20210225800978, 2014년 합병과 무관함을 note에 명시). `price_integrity.
rebuild_views()` 재실행 후 035720의 audit 플래그 다수 → 8건으로 감소. 전체 회귀
237건(동시세션 신규 테스트 포함) 통과.

### 256940 — DART에 분할/병합/감자 이력 전혀 없음, 보류

전체 이력(2010~2026) 조회 결과 액면분할·주식분할·병합·감자 공시가 **0건**. 000670/
035720과 같은 "미래 이벤트를 앞서 반영" 패턴을 설명할 근거가 없다 — 근거 없이 손대지
않는다는 원칙대로 보류. 원래 발견한 가격불일치 자체는 진짜일 수 있으나(다른 원인),
이번엔 조사 안 함.

### 300720(한일시멘트) — 같은 종류의 버그가 아님, 보류

DART에서 2021-07-09 주식분할결정(10:1, 액면가 5,000→500원, 신주상장 2021-09-13) 확인 —
이벤트 자체는 진짜다. 하지만 가격 서명이 000670/035720과 다르다: 정지기간(2021-09-08~10)
전후로 우리 DB가 pykrx 대비 **양쪽 다** 0.778배 차이가 남아있다(000670/035720은 전환
이후 깨끗하게 1.0배로 수렴했는데, 이건 분할 전후 내내 별도의 배율이 남아있음) — 단순
"미래 분할 조기반영" 한 가지로 설명되지 않는 별개/복합적인 문제로 판단, 이번엔 안 고침.
정확한 원인 규명이 먼저 필요.

### 최종 정리

이 세션에서 marcap+DART 이중교차검증으로 **완전히 확정하고 실제 수정까지 완료한 건
2건**(000670, 035720) — 총 2,551행(1,015+1,536) 수정. **분할이력이 있지만 패턴이
달라 보류한 건 1건**(300720). **분할이력 자체가 없어 보류한 건 1건**(256940). 이
경험으로 다음 세션이 규모 파악 시 참고할 판정 기준: (1) marcap이 의심구간 전후로
매끄럽게 연속되는가(발행주식수 불변 포함) (2) DART에 그 "전환일"이 아니라 그보다 훨씬
나중의 진짜 분할/병합 공시가 있는가 (3) 전환 후 비율이 그 진짜 이벤트의 비율과
일치하고, 전환 이후로는 깨끗이 1.0배로 수렴하는가 — 이 세 개가 전부 맞아야 000670류로
확정, 하나라도 안 맞으면(300720처럼) 별도 조사 필요.

---

## v4 과거 데이터 인프라 완성 및 PIT 검증 승격 (Codex, 2026-09-23)

사용자 피드백("과거 데이터 인프라가 완벽하지 않으면 수정해야지?")에 따라 이전 기록의
잔여 한계를 실제로 해소했다.

### 발견·수정한 추가 결함

1. `krx_security_reference_collector.py`가 다시 `code.isdigit()`을 사용해 `00088K`,
   `00104K`, `02826K` 같은 영숫자 우선주를 현재 기준정보, 상폐목록, 일별 발행주식
   수에서 모두 누락했다. 공용 `is_kr_equity_code()`로 여섯 군데를 통일했다.
2. 일별 KRX 백필이 날짜별 약 2,500건을 개별 INSERT해 지나치게 느렸다. 날짜 병렬 수집과
   `executemany` upsert로 바꿨고, `price_trading_calendar`의 실제 거래일만 요청한다.
3. 현재 기준정보 갱신이 FDR 상폐 API가 빈 응답이어도 과거 reference를 먼저 삭제하는
   구조였다. source별 독립 갱신으로 바꿔 빈 upstream 응답이 정상 이력을 지우지 못하게 했다.
4. Yahoo ETF 보조분류가 2,800만행 `price_history` 전체 GROUP BY를 수행해 timeout이 났다.
   이미 집약된 `security_master_history`를 사용하고 ETF 가능성이 있는 종목만 조회하도록
   축소했다.
5. KRX 일별 관측으로 만든 구간이 기존 공식 상장일~현재 구간과 겹쳐 share history
   재구축에서 중복키가 발생했다. 기존 공식 구간이 완전히 덮는 일별 관측구간은 제거하고,
   시장 이전 전 구간처럼 실제 공백을 채우는 interval만 유지했다.
6. KONEX에서 KOSDAQ으로 이전한 종목의 이전 가격구간을 현재 market=KOSDAQ으로
   역투영하던 오류를 확인했다. 완전한 KRX KOSPI/KOSDAQ 일별 목록에 없던 사전구간은
   `pre_official_equity_reference_ineligible`, `is_tradable=0`으로 명시했다.
7. v4가 `delisting_outcomes`를 `_run_portfolio()`에 넘기지 않아 정확 PIT 유니버스에서 다시
   선택된 282690을 0원 처리했다. 확인된 DN오토모티브 합병가치 2,991.68448원을 실제
   청산에 연결했다(최종 거래 수익률 -77.43%, exit reason에 실제가치 반영 명시).
8. 완료된 0거래 컴포넌트가 가격·상폐·기업행위 노출 0임에도 무조건 세 감사를 실패하던
   정책을 고쳤다. 컴포넌트는 `no_exposure=true`로 증빙해 vacuous pass하고, 전략 전체가
   모든 기간 0거래인 경우의 strategy-level 최소 보유창 요구는 그대로 유지했다.

### 데이터 백필과 검증

- KRX 공식 일별 종목기본정보/발행주식 수를 2015~2026 전체 거래일에 대해 재수집.
- 최종 `official_daily_snapshot`: 7,012,298행, 2,951일, 3,314종목,
  2015-01-02~2026-09-22.
- 일별 관측에서 3,739개 상장구간을 계산하고, 중복 제거 후 606개 역사 구간을 reference에
  추가했다.
- 재구축 전 백업: `security_master_history_backup_codex_20260923` 4,866행,
  `security_share_history_backup_codex_20260923` 96,918행.
- 최종 v4 6개 기간 모두 master approx=0, share approx=0.
- C3 검증: 상폐구간, 주식수 변경, 생존편향 감소, 과거 유니버스 시계열 모두 PASS.
- 회귀: 56 tests + 27 subtests PASS.

### 최종 v4 결과

- 선택 suite: `9525d0ffa0c98553`
- 등급: **`point_in_time_verified`** (6개 컴포넌트 전부 동일 등급)
- 기간 수익률: `+78.26 / 0.00 / -1.10 / -6.35 / -2.37 / +48.33%`
- 6기간 단순평균: `+19.46%`, 양수 2/6
- 남은 gate: `forward_validation`뿐. 이는 과거 데이터 결함이 아니라 별도의 미래 구간
  검증 단계다.

---

## ⚠️ 최우선 발견: price_history 전체 29%(297만행, 2,662종목)가 소수점 보간값 오염 — 2026-03-31~04-07 특정 배치 사고로 확정 (Claude, 2026-09-23)

golden_cross의 21.12~22.10을 막던 4개 종목(138930/033290/175330/073570)을 000670과
같은 방법론으로 조사하다가 **이번 세션 전체를 통틀어 가장 크고 중요한 발견**을 했다.

### 조사 과정

- **073570**: pykrx로 확인한 결과 진짜 상한가(+30.00%, 2022-09-30) — 데이터 문제
  아님, 실제 시장 이벤트(원인은 별도 조사 필요하나 가격 자체는 진짜).
- **138930(BNK금융지주)/175330(JB금융지주)**: 감사 데이터의 "전일종가" 필드가
  pykrx 실제값과 전혀 다름(175330: 우리 6,365.10986328125원 vs pykrx 8,400원 정수) —
  **소수점이 긴 값 자체가 결정적 단서**(한국 주식 체결가는 항상 정수원 단위,
  6365.10986328125 같은 값은 애초에 실제 체결가일 수 없다).
- 175330의 2022-01-20~02-10 구간을 날짜별로 까보니 **정수값(진짜, pykrx 일치)과
  소수점값(가짜, 보간)이 날짜별로 뒤섞여 있음**(01-25/26, 02-07/08/09만 정수=진짜,
  나머지는 전부 소수점=가짜) — 000670/035720의 "장기간 한 방향 전환" 패턴과는
  완전히 다른, 날짜 단위로 플리커링하는 별개의 버그.
- 175330 전체 이력(3,235행) 중 **1,429행(44%)**이 소수점 종가.
- **전체 `price_history` 스캔**: close>0인 1,024만7,776행 중 **296만9,580행(29%)**이
  소수점 종가, **2,662개 종목**(사실상 전체 유니버스의 상당 부분)이 영향받음.
- **결정적**: 이 소수점 행들의 `created_at`을 날짜별로 집계하니 **2026-03-31(77만)/
  2026-04-05(140만)/2026-04-07(73만) 사흘에 290만행(전체의 97.6%)이 집중** — 하나의
  특정 대규모 백필/재적재 사고임이 확정됨(장기간에 걸쳐 서서히 쌓인 게 아니라, 특정
  3일간 실행된 배치 작업 하나가 원인).
- `data_fix_log`에는 이 시점 기록이 없음(정상적인 "수정" 스크립트가 아니라 일반
  수집/백필 스크립트로 추정) — 이 기간(2026-03-29~04-08) git 커밋도 0건이라 정확히
  어느 스크립트였는지는 이번 세션에서 특정 못함(스케줄러/launchd 실행이력을 다음
  세션이 직접 확인해야 함).

### 왜 이게 지금까지 발견 안 됐는지에 대한 가설(확인 안 됨)

price_series_registry가 이미 `price_history`의 `price_basis`를 `"adjusted_intended_
mixed_risk"`로 등록해두고 "Do not overwrite from raw sources... mixed_basis_risk"라고
경고해둔 것과 정확히 맞아떨어진다 — 즉 이 위험 자체는 이미 알려져 있었지만, 실제
규모(297만행/2,662종목)와 정확한 발생 시점(2026-03-31~04-07)이 이번에 처음 정량화됐다.

### 재현 가능한 진단 쿼리

```sql
-- 전체 규모
SELECT count(*) FROM price_history WHERE close>0 AND close != FLOOR(close);
SELECT count(DISTINCT stock_code) FROM price_history WHERE close>0 AND close != FLOOR(close);
-- 시점 분포(사고 확인)
SELECT DATE(created_at) d, count(*) c FROM price_history
  WHERE close>0 AND close != FLOOR(close) GROUP BY DATE(created_at) ORDER BY c DESC LIMIT 15;
```

### 아무것도 고치지 않았음 — 다음 세션 최우선(이전 "다른 종목 스캔"보다 우선순위 높음)

1. **원인 스크립트 특정**: launchd/cron/scheduler 실행이력(이 세션에서 못 본 로그)으로
   2026-03-31~04-07 사흘간 무엇이 돌았는지 먼저 확인 — 같은 버그가 재발하지 않으려면
   필수.
2. **패턴 확정**: 175330처럼 날짜별로 정수/소수점이 뒤섞이는 게 일반적인지, 아니면
   종목마다 다른 양상인지 표본을 몇 개 더 까봐야 함.
3. **회복 가능성 확인**: pykrx/marcap으로 이 297만행 전체를 대체 가능한지(정수인
   "진짜" 값이 이미 같은 종목의 다른 날짜에 섞여 있다는 건, 최소한 그 3일간의 원본
   수집 자체는 어딘가 존재했을 가능성을 시사함 — 완전히 새로 수집할 필요는 없을 수도).
4. **영향 범위**: 이 사고가 이번 세션에서 발견한 golden_cross/megatrend/se_momentum/
   v10 같은 전략들의 governance에 얼마나 영향을 주고 있는지 — `unresolved_active_
   common`(6,352건)의 상당 부분이 이 사고 하나로 설명될 가능성이 높다.

## ✅ 위 사고 실제 수정 완료 — 297만행 중 218만행 복구, 나머지는 지수/미커버 확인 (Claude, 2026-09-23)

사용자 지시("3.31~4.7일 데이터 확인하고 정확한 값으로 수정해, 문제 있는건 멈추지
말고 계속 수정해")에 따라 위 발견을 실제로 수정했다.

### 1단계: price_history 원가 복구

`scripts/apply_bulk_marcap_interpolation_repair_20260923.py` (신규) — 연도별로
marcap(비조정 원가) parquet을 Postgres TEMP TABLE에 `.copy()`로 적재한 뒤,
`ph.close>0 AND ph.close != FLOOR(ph.close)`인 행만 marcap과 `(stock_code,date)`로
조인해 백업(`price_history_fix_backup`)+`UPDATE`를 서버사이드 단일 쿼리로 실행
(행 단위 Python이 아님 — 수백만행 규모라 필수). 2010~2026년 전체를 `--apply`로 실행:

| 연도 | 수정행 | 연도 | 수정행 |
|---|---|---|---|
| 2010~2018 | 0 (전부) | 2023 | 348,752 |
| 2019 | 363,066 | 2024 | 324,488 |
| 2020 | 150,843 | 2025 | 286,026 |
| 2021 | 340,789 | 2026 | 15,980 |
| 2022 | 351,085 | **합계** | **2,181,029행** |

(`SELECT sum(row_count) FROM data_fix_log WHERE run_id LIKE 'bulk_marcap_interpolation_repair%'`
= 2181029로 검증 완료.) 2010~2018년은 marcap과 매칭되는 소수점행이 아예 0건 —
그 연도들의 소량 잔존 소수점행(연 2,700~3,000건)은 이 배치 사고와 무관한 별개
현상으로 판단, 손대지 않음(근거 없이 값을 만들지 않는다는 원칙 유지).

**나머지 788,551행/769종목은 의도적으로 미수정**: marcap에 매칭이 안 됨을 확인한
결과, 잔존 상위 종목이 전부 `^`로 시작하는 해외/시장 지수(`^IXIC`,`^GSPC`,`^DJI`,
`^KS11`,`^SOX`,`^STOXX50E`,`^HSI`,`^TWII`,`^N225`,`^KQ11`,`^KS200`,`^KQ150` 등 —
지수는 소수점 종가가 정상이라 애초에 "버그"가 아님, marcap은 지수를 아예 커버 안 함)
이거나, 211900 같은 상장폐지/희귀 종목(pykrx도 marcap도 해당 연도 데이터 자체가
0건 — 대체할 독립 소스가 없어 못 고침, 억지로 값을 만들지 않음)이었다.

### 2단계: price_jump_audit 스테일 정리 (return_usable 실제 복원)

1단계만으로는 `canonical_price_history_v.return_usable`이 자동 복원되지 않음을
발견 — 그 뷰 로직상 `price_jump_audit`에 해당 (stock_code,event_date) 행이
**존재하기만 하면**(값이 새 price_history와 안 맞는 stale 행이어도) `return_usable`을
무조건 0으로 강제한다(뷰 자체는 `canonical_quality='stale_audit'`으로 라벨링은
하지만 return_usable을 되돌리진 않음). `price_jump_audit`은 물리 테이블이라
`rebuild_views()`로는 안 고쳐짐, 전체 재감사(`audit_price_jumps_and_build_canonical.py`)는
`refresh_calendar`의 전체 스캔 때문에 타임아웃 나는 걸로 이미 알려져 있어(다른
세션도 별도로 이 성능 문제 작업 중) 전체 재실행은 회피.

대신 `scripts/reclassify_stale_price_jump_audit_20260923.py` (신규) — stale 행만
정확히 찾아서(`price_jump_audit`값 vs 현재 `price_history`값 직접 비교, 전체
재스캔 아님) `audit_price_jumps_and_build_canonical.py`의 분류 로직을 그대로
복사해 그 행들에 한해서만 재분류:

- stale 행 3,169건 발견 → 전량 처리
- **2,368건 삭제**: 수정된 값으로는 quality_status가 `normal`/`insufficient_history`로
  돌아옴 — "점프" 자체가 나쁜 데이터가 만든 허상이었다는 뜻, 감사 행 자체가 무의미해짐.
- **801건 재분류**: 수정된 값 기준으로 여전히 뭔가 플래그가 남음 — 새 분류값으로
  UPDATE (unresolved_active_common 603, corporate_action_pending_confirmation 17,
  quarantined_basis 10, mixed_basis_or_price_corruption 30, confirmed_corporate_action 30,
  corporate_action_or_delisting_nearby 19, coverage_gap 91, inactive_or_noncommon_review 1).
- 검증: 재실행 후 stale 행 **0건**(재확인 완료). `unresolved_active_common` 총계
  6,352 → **4,961**로 감소.
- 138930/175330/033290 개별 확인: canonical_price_history_v에 남은 non-normal
  행이 각각 3/1/28건으로 급감했고, 남은 것도 전부 정상적인 구조적 상태
  (`insufficient_history`, 진짜 `unexplained_jump` 1건, 실제 `suspended`/
  `coverage_gap`) — 더 이상 이 배치 사고의 잔재가 아님.

### 원인 스크립트 특정 완료 (Claude, 2026-09-23, 사용자 지시 "남은 항목도 조치해")

`git log --since=... --until=...`가 계속 0건이었던 이유부터 확인: 이 저장소의
git 이력 자체가 **2026-04-16**(`89438f2 Add stock_dashboard initial code`)에야
시작됐다 — 즉 사고 시점(03-31~04-07)은 이 코드베이스가 git으로 추적되기 전이라
"커밋 0건"은 원인 조사에 아무 의미가 없는 착시였다(다음에 같은 걸 다시 확인할
필요 없음).

`collect_prices.log`(runtime 디렉터리, mtime 2026-03-31 22:01)를 열어보니 정확히
이 사고 시각대에 `scripts/archive/collect_all_prices.py`(현재는 archive로 옮겨져
있고 crontab/launchd 어디에도 등록 안 돼 있음 — 확인 완료, 지금은 비활성)가
6,311개 종목 전체를 훑은 실행 로그였다. 이 스크립트를 읽어보니 사고 메커니즘이
그대로 나온다:

```python
h = yf.Ticker(f'{code}{suffix}').history(period='2y', timeout=10)  # auto_adjust 기본값 True
...
conn.execute('''INSERT OR IGNORE INTO price_history (...) VALUES (...)''', ...)
```

- **`yf.Ticker().history()`의 기본 `auto_adjust=True`**가 배당/분할로 역조정된
  소수점 Close를 반환한다 — 한국 종목의 실제 체결가(항상 정수원)와 절대 일치할
  수 없는 값의 출처가 바로 이것.
- **`INSERT OR IGNORE`**(db_compat이 Postgres `ON CONFLICT DO NOTHING`으로 번역 —
  `db_compat.py:301` `translate_sqlite_sql` 확인)는 **이미 존재하는 행은 절대
  덮어쓰지 않고, 비어있던 행에만** 삽입한다 — 이게 바로 175330 2022-01-20~02-10
  구간에서 발견했던 "정수(진짜)/소수점(가짜)이 날짜별로 뒤섞이는" 플리커링의
  정체다: 원래 데이터가 있던 날짜는 그대로 진짜값 유지, 마침 비어있던 날짜에만
  가짜 소수점값이 처음 채워짐.
- 대상 종목 선정 쿼리(`WHERE COALESCE(p.cnt,0) < 60`, 2024년 이후 60행 미만인
  종목 전체 백필)가 광범위해서 2,662종목까지 영향이 퍼진 것도 설명됨.
- 3일(03-31/04-05/04-07)에 걸쳐 나뉘어 발생한 것도, 이 스크립트가 "부족한
  종목만" 골라 반복 실행되는 성격상(한 번 돌 때마다 일부가 채워지고, 다른
  수집기가 지운/비운 gap이 다시 생기면 다음 실행에서 또 채워짐) 자연스럽게
  설명 가능.

**남은 불확실성 1건**: 스크립트에 하드코딩된 로그 경로(`/Applications/stock_
dashboard/collect_prices.log`)가 현재 이 머신에 존재하지 않는 디렉터리라, 지금
그대로 실행하면 즉시 FileNotFoundError가 난다 — 즉 03-31 당시 실행된 실제
버전은 이 경로가 달랐거나(나중에 04-16 최초 커밋/08-28 archive 정리 때 경로만
수정됐을 가능성), 그 시점엔 `/Applications/stock_dashboard`가 실제로 존재했을
가능성이 있다. 어느 쪽이든 핵심 메커니즘(yfinance auto_adjust 소수점 + INSERT
OR IGNORE로 인한 gap-only 오염)은 위 코드 자체에서 직접 확인되므로 추가
확인 없이도 결론으로 충분하다고 판단. 재발 방지: 이 스크립트는 이미 archive로
격리돼 있고 어떤 스케줄러에도 연결 안 돼 있음을 확인함(crontab -l, ~/Library/
LaunchAgents/*.plist 전수 확인, 매치 없음) — 추가 조치 불필요, 실수로 다시
활성화되지 않게 archive 밖으로 꺼내지 않을 것.

### golden_cross 등 전략 재감사 결과

`scripts/audit_selected_strategy_price_integrity.py` 재실행 (2026-09-23 21:16) —
27개 전략 중 **22개 통과**(임계값 7%). 이 스크립트는 실행 시 각 전략의 현재
`run_hash`에 `register_artifact(run_hash,"price_integrity",passed,...)`를
직접 UPSERT하므로, 재실행 자체가 곧 "재등록"이다 — `register_run_set`/
`select_run`을 별도로 다시 부를 필요 없음(suite_hash/run_hash가 안 바뀌었고,
`derive_status()`가 `run_verification_artifacts`를 라이브로 읽음).

**핵심 확인**: 이 조사를 시작하게 만든 장본인 **golden_cross**의 등록된
run(`selected_run_registry`, report_type=strategy_center)을 `derive_status()`로
직접 재확인 —

- golden_cross: `price_integrity_gate: False → True`, 전체 status
  `legacy → point_in_time_approx`로 실제 승격 확인. contamination_ratio
  0.0357(3.57%, 7% 임계값 통과) — 021.12~22.10 구간을 막던 138930/175330/
  073570/033290이 이제 더 이상 게이트를 막지 않는다.

나머지 5개 실패(deep_recovery 12.11%, extreme_dd_volume 15.98%,
high_profit_compound 7.69%, low_base_breakout 4.55%+survivorship 5건,
v12 0.68%+survivorship 1건) — `price_integrity_gate=False` 그대로(`derive_status`
로 재확인 완료). deep_recovery/extreme_dd_volume은 정책 주석에 이미 "명백히
다른 범주의 훨씬 나쁜 이상치"로 기록돼 있던 종목들(2026-09-12 기준 9.86%/
13.21% — 이번 수정과 무관하게 원래도 실패해야 정상, 임계값 자체가 이 둘을
계속 걸러내도록 보정된 것)이고, low_base_breakout/v12는 survivorship_findings
(전략 로직 결함 — 거래 가능 구간 밖에서 진입/청산, 가격 데이터 품질과 무관)가
원인이라 이번 가격 수정의 범위 밖. 즉 이번 수정으로 "고쳐졌어야 할" 전략은
golden_cross 하나였고, 정확히 그것만 실제로 고쳐졌다 — 나머지 실패는 전부
설계상 의도된 별개의 이유.

`audit_selected_strategy_data_availability.py`도 재실행 확인(에러 없이 완료) —
단, 이건 지연공시 재무데이터 row-level provenance를 보는 별개 축의 감사라
이번 price_history 수정과는 무관, 참고용으로만 재확인.

### 전체 테스트 스위트 재검증

`pytest -q`(저장소 루트 그대로)는 `scratch/`의 임시/일회성 디버그 스크립트들을
테스트로 잘못 수집해 7건 collection error를 냄(예: `/Applications/stock_dashboard/...`
하드코딩 경로 — 위 아카이브 스크립트와 같은 유령 경로, 실제 테스트 코드 결함
아님). 올바른 스코프로 재실행: `pytest tests/` **276 passed**(+27 subtests),
루트의 의도된 `test_collection_health.py`/`test_interp.py`/`test_krx_api.py`
**6 passed** — 도합 282개 전부 통과, 이번 세션의 모든 수정(가격 218만행 복구 +
감사 테이블 3,169건 재분류) 이후에도 회귀 없음 확인.


## ✅ 재발방지 가드 + 전체 재감사 + 2차 복구 (Claude, 2026-09-24)

**정정**: 위 "211900은 pykrx도 marcap도 데이터 0건"은 틀림 — pykrx가 현재 전 종목(069500 포함)에서 빈
DataFrame을 반환하는 상태였을 뿐, 211900/136340은 ETF이며 FinanceDataReader로는 조회된다. 이후 다른 세션이
ETF/naver 기준 복구(451K+156K행)를 별도로 수행함.

1. **쓰기 가드**(`price_integrity.py` `guard_historical_price_write`): 6자리 코드의 O/H/L/C 중 하나라도 정수가
   아니면 `app.price_basis_checked=1` 여부와 무관하게 차단(자체 테스트: 소수점 차단/정수 허용 확인, DB 설치 완료).
2. **감사 타임아웃 해결**: `refresh_calendar(conn, full=False)` 증분화(최신 달력일-45일부터만 스캔) +
   `audit_price_jumps_and_build_canonical.py` 타임아웃 60→1800초. 전체 재감사 실행 성공.
3. **2차 복구로 발견한 함정(내 실수 포함)**:
   - `apply_residual_fractional_repair_20260924.py`(marcap 13,396행 적용, FDR 131,479행 적용). FDR(Naver)은
     **분할조정 가격**이라 raw(marcap/실거래) 이웃과 섞여 가짜 점프 ~4.7K건 생성 → `fix_fdr_basis_conflicts_20260924.py`로
     marcap raw 11,043행 교체.
   - 사건기간(created_at 03-20~04-08) 행 중 **정수인데 분할조정 기준**인 행(분할계수 덕에 우연히 정수)은
     소수점 검사에 안 걸림 → `fix_incident_window_basis_20260924.py`로 marcap과 OHLC가 다른 133,387행 교체
     (거래량만 다른 5.5만행은 노이즈라 제외).
   - 교훈: "정수 검사"만으로는 기준(raw vs 조정) 혼합을 못 잡는다. 복구 후엔 반드시 전체 재감사로 신규 점프 확인.
4. **결과**: 전체 재감사 점프 45,188→**23,166**, unresolved_active_common 20,231→**3,711**,
   mixed_basis_or_price_corruption 4,431→**2**. 잔존 6자리 소수점행 8,396행/86종목(소스 미확보, 미수정).
   전략 감사 23개 통과/4개 실패, golden_cross 비율 3.57%→**1.95%**. 테스트 293 passed.
5. **남은 것**: unresolved 3,711건(실제 분할 등 미등록 corporate action 가능), 소수점 잔존 8,396행,
   coverage_gap 16,592건(별개 문제), 원인 스크립트(archive)는 가드로 재발 차단됨.

### 추가 (2026-09-24 오후): unresolved 점프 marcap 대조 복구
`scripts/fix_unresolved_jumps_vs_marcap_20260924.py` — unresolved 3,711건 중 marcap 원본 시계열은 가격제한폭 이내로
매끄러운데 우리 DB 행만 튀는 2,960건의 2,783행을 marcap OHLCV로 교체(run_id unresolved_jump_marcap_fix_20260924_113858).
재감사: 점프 23,166→**22,001**, unresolved 3,711→**2,547**. 남은 건은 marcap도 같은 점프를 보이는 실제 사건
(액면분할/감자 등 미등록 corporate action 가능)이거나 marcap 미커버 종목 — 데이터 수정 대상이 아니라 분류 대상.

### 추가 (2026-09-24 저녁): 발행주식수 증거 분류 + 연속구간 복구
1. **`marcap_client.share_count_evidence()`** + `audit_price_jumps_and_build_canonical.py` 신규 분류
   `corporate_action_share_count_evidence`(return_usable=0): 점프 전후 ±10일 marcap 발행주식수가 s배(≥15% 변화)이고
   s×가격비율≈1(±10%)이면 시총 연속 = 액면분할/병합/감자 등 증거. `confirmed_corporate_action`과 별개 이름이라
   `allow_confirmed_corporate_actions` 게이트(팩터 확정 필요)에는 영향 없음. 현재 366건.
2. **`fix_unresolved_jumps_vs_marcap_20260924.py` 확장**: 경계 2행만 고치면 점프가 안쪽으로 이동(001790 2015-01, 10배 낮은 구간).
   marcap이 매끄러운 점프에 닿은 "marcap 대비 종가 10% 초과 이탈 연속구간"(≤600행)을 통째로 교체. 89,682행 중 88,715행이
   **created_at 2026-07-12 단일 배치**(별개 2번째 오염 사고, 2015/2018 클러스터, 원인 스크립트 미특정).
3. 결과: unresolved 2,547→**1,718**, 전체 점프 22,001→**21,423**, 전략 감사 23통과/4실패 유지, golden_cross 1.95%, 테스트 287 passed.
4. **미해결**: 2026-07-12 배치의 원인 스크립트 특정(가드는 소수점만 막으며, 정수 배율 오류는 못 막음), unresolved 1,718건
   (marcap도 동일 점프인 실제 사건 ~700건 + 나머지), 소수점 잔존 ~8.4K행, coverage_gap 16,592건.

### 2026-07-12 배치 정체 조사 (Claude, 2026-09-24)
- 2,231,086행이 **정확히 같은 초(`2026-07-12 07:21:53`)**에 적재됨(6자리 코드, 2,678종목, 날짜 2015-01-02~2026-07-12).
  옛 SQLite `stock.db`에도 동일 created_at/값이 있어 **Postgres 이전이 아니라 SQLite 시절 대량 재적재**. 원인 스크립트는
  로그/커밋 부재로 미특정(logs/·git 이력에 흔적 없음).
- marcap raw와 비교: 2019년 이후 행은 거의 일치, 그러나 **2015~2018년 행 약 65만 건(747~802종목)이 raw와 10% 이상 다름**
  (배율이 종목마다 다르고 상수도 아님 — 분할/감자가 여러 번 반영된 조정 기준 혼재로 보임, 639종목은 corporate_action_events 보유).
  이 구간은 감사 점프로 안 잡히는 내부 연속 시계열이라 **미수정**. raw로 되돌리면 실제 분할 지점에 점프가 생겨 return_usable=0 구간이
  늘어남 → "과거 이력을 조정기준 vs raw 중 무엇으로 통일할지" 정책 결정 필요(price_series_registry는 adjusted_intended_mixed_risk).

### 결정: 과거 이력은 raw(실거래가)로 통일 — 2015~2018 배치 행 복구 (Claude, 2026-09-24)
근거: (1) 2019+ 약 290만 행이 이미 raw로 복구됨/기존 복구 방침도 raw, (2) marcap과 행 단위 검증 가능(조정기준은 분할계수 재계산 필요 → 신규 오류 위험),
(3) 전략 백테스트 시작 2020-03이라 2015~2018은 지표 lookback용이라 영향 작음. `fix_batch0712_to_raw_20260924.py`로
created_at='2026-07-12 07:21:53' & date<2019 & marcap과 OHLC 상이한 **827,724행**을 marcap raw로 교체(run_id batch0712_raw_basis_fix_20260924_121832).
재감사: unresolved 1,718→**1,251**, 전체 점프 21,423→**21,026**(신규 점프 폭증 없음), share_count_evidence 434건.

### coverage_gap 채우기 (Claude, 2026-09-24)
`scripts/fill_coverage_gaps_20260924.py`: 감사 coverage_gap 16,592건 중 간격≤30일(진짜 빠진 거래일) 대상, 달력 기준 빠진 (코드,날짜) 36,295쌍 중
**35,496행 삽입**(marcap raw 14,593 + FDR 20,903; FDR은 앞뒤 실제 DB 종가 ±30% 밴드 검증, 정수/OHLC 형태 검증). run_id coverage_gap_fill_20260924_123721,
백업 테이블에 old=NULL로 기록(롤백=해당 행 삭제). 재감사: coverage_gap 16,592→**612**, 전체 점프 21,026→**5,133**, unresolved 1,251→1,290(가려졌던 진짜 점프 39건 노출).
테스트 295 passed, 전략 감사 23/4 유지, golden_cross 1.95%.
잔존: 소수점 8,396행/86종목은 FDR 캐시에 해당 날짜 데이터 자체가 없음(ETF 상장 이전/구간 미제공) → 소스 없음. unresolved 1,290건은 marcap도 동일 점프(실제 사건)이거나 증거 부재.

## 기업이벤트 등록 + coverage_gap 사유 기록 (Claude, 2026-09-24 저녁)
사용자 지적 2건 반영:
1. **미해결 점프 → 기업이벤트 등록**: `scripts/register_corporate_events_from_dart_20260924.py`. marcap raw와 일치하는 "진짜" 점프 997건에 대해
   로컬 dart_disclosures(2016-05~) 우선, 없으면 OpenDART list.json 실시간 조회(corpCode.xml 매핑, B/I 유형, -300/+10일)로 분할/병합/감자/합병/
   주식교환/거래정지 공시를 찾아 **951건을 corporate_action_events에 등록**(source='dart_disclosure+marcap_jump_2026-09-24', review_required,
   거래정지류는 not_price_adjusting, 가격팩터는 미도출). 증거 없는 46건은 등록 안 함. 롤백: 해당 source 행 DELETE.
   (id 시퀀스가 max(id)보다 뒤처져 충돌 → setval로 보정함.)
2. **coverage_gap 재검토 방지**: `price_coverage_gap_reviewed` 테이블 신설(price_integrity.TABLES) + 감사 스크립트가 해당 행을
   `coverage_gap_reviewed`(return_usable=0)로 분류. `review_coverage_gaps_20260924.py`가 사유+근거 기록:
   source_row_invalid 358(원본에 행은 있으나 종가>고가 등 OHLC 무효 → 값을 만들어 넣지 않음), source_basis_mismatch 109(FDR이 분할조정 기준),
   no_source_data 38, dormant_no_record 18, trading_halt 1(DART 거래정지 rcept 기록).
3. 그 전에 채울 수 있는 행 채움: `fill_coverage_gaps_20260924.py --max-gap` (FDR 밴드검증, 수렴할 때까지 반복; FDR 캐시 범위 stale 버그 수정) +
   `fill_suspension_gaps_from_marcap_20260924.py`(marcap 정지 마커행 21,783행).
4. 결과(전체 재감사): 미해결 점프 **353→146**(marcap과 다른 데이터 글리치 620행 추가 교체 포함), pending_confirmation 1,057,
   coverage_gap 16,592→**1**(+reviewed 524). 테스트 399 passed, 전략 감사 23/4 유지, golden_cross 1.95%.
5. 남은 것: unresolved 146건(DART 증거 없는 진짜 점프 ~60 + 글리치/무marcap), 소수점 8,396행은 격리 상태 유지.

### 마무리 (2026-09-24)
- 신규 회귀 테스트 `tests/test_audit_extensions_20260924.py`: refresh_calendar 증분/full, share_count_evidence 조건, 발행주식수 증거 분류가 return_usable=0 유지,
  coverage_gap_reviewed 기록 시 재큐잉 안 됨(쓰기 가드 트리거는 Postgres 필요 → 수동 검증만).
- marcap이 종가만 있고 OHL 없는 날짜에서 DB 종가와 다른 23행은 `price_integrity_quarantine`(reason=disagrees_with_marcap_close_no_ohl_source)로 격리.
- 잔존(내가 더 못 고치는 것): 미해결 점프 ~146건 중 DART 증거 없는 진짜 점프 ~60건/marcap 미커버 10건, 소수점 8,396행(격리), 7/12 배치 원인 스크립트 미특정.

## 연구 도구 도입 1~2단계 (Claude, 2026-09-24)
- 연구용 venv: `stock_dashboard/research_venv`(numpy 2.4.6, quantstats/alphalens-reloaded/vectorbt/PyPortfolioOpt). 운영 venv(numpy 1.26.4/pandas 2.3.3)는 미변경.
  DB는 운영 venv의 `research/extract_research_inputs_20260924.py`가 parquet(data_cache/research)로 추출 → 연구 venv는 parquet만 읽음(자격증명 불필요).
- alphalens-reloaded 로컬 패치: `alphalens/utils.py`의 `df.index.levels[0].freq = freq`를 try/except(월말처럼 희소한 날짜는 BusinessDay freq 설정 불가). 재설치 시 재적용 필요.
- 결과: research_outputs/alphalens_factor_validation_20260924.{md,csv,json}. 요약: 저변동성/저PER/저PBR 유효, 60~120일 모멘텀은 역효과, 수급(supply_20d)·기존 model_score는 음의 IC.
- 문서(PDF "GitHub 추천 20선")의 pykrx 중심 전제는 현재 사실과 다름(pykrx 전 종목 빈 결과) → marcap/FDR/DART 유지.
- 1단계(QuantStats): `research/build_backtest_equity_curves_20260924.py`로 `backtest_equity_curve` 테이블 신설(engine 929, realized_pnl 1,122, realized_pnl_assumed_100m 575 = 2,626/3,035 run;
  409건은 거래별 손익 없음). `research/quantstats_strategy_report_20260924.py` 결과: research_outputs/quantstats_summary_20260924.{md,csv,json} + HTML 3종.
  복원곡선(15개 전략)은 계단형이라 Sortino/베타 신뢰 불가 → 엔진이 일별 평가곡선을 저장하도록 개선 필요.
- 3단계(vectorbt): `research/vectorbt_rule_sweep_20260924.py`(vectorbt 1.0.0은 plotly<6 필요 → 연구 venv에 plotly 5.x 설치, 트레일링은 sl_trail). 결과 research_outputs/vectorbt_rule_sweep_20260924.{md,csv,json}:
  국면필터는 훈련 MDD만 개선·검증 수익 반토막, 훈련 최적값이 검증에서 유지되지 않음 → 운영 기본값 변경 근거 아님(기존 엔진 재현 + 하락장 표본 필요).
- 4단계(PyPortfolioOpt): `research/pyportfolioopt_sidebyside_20260924.py`. 동일비중(CAGR 18.1%, Sharpe 0.82)이 HRP(14.0%/0.74)·MinVol(4.9%/0.33)보다 우수 → 최적화 비중 도입 보류.
  vectorbt(고정금액 진입)와 이 스크립트(월 복리 재배분)의 절대수익 정의가 달라 통일 필요.
- **미국 가상매매 9/23 차단 원인 확정·수정**(`routes/us_virtual_trading.py`): 거래일 인정 기준이 "전 종목 수 vs 역대 최대(3,623, 2026-05-22)의 95%(=3,442)"였음.
  9/21 3,546 → 9/22 3,446 → 9/23 3,424로 스팩/권리/워런트 등 비유동 종목이 빠져(9/22 106종목 중 43종목은 이후 다시 등장 = 그날 거래 없음) 9/23이 기준 미달.
  전 종목 수는 상장폐지가 쌓일수록 영구히 기준 아래로 내려가는 구조적 결함(docstring이 경고한 바로 그 문제)이라, 기준을 **핵심 유니버스**(최근 60세션 중 90% 이상 출현 종목, 현재 3,527개)의
  당일 출현 비율 ≥95%로 변경. 9/23은 3,411/3,527=96.7%로 통과. 테스트 tests/test_us_paper_core_coverage_20260924.py(3건). 서버(uvicorn) 재기동 후 반영됨.
  주의: 핵심 종목 98개가 9/22에 한꺼번에 사라진 것은 상장폐지/합병인지 수집 누락인지 미확인(ANY, ALF, GV, GLMD 등 실제 회사 포함) → 수집기 로그 확인 필요.
- QuantStats 후속: `research/reconstruct_mtm_equity_20260924.py`로 거래로그+가격 기반 일별 평가곡선 재구성(선정 전략 79개 run, source='mtm_reconstructed'). 첫 시도는 조정종가 수준과 로그 진입가 수준이 달라(분할 종목) MDD -90%로 왜곡 → 같은 가격 시리즈의 진입일 값을 기준으로 수정. 결과 베타 0.3~0.9로 정상화.
- 미국 데이터: 9/22~23 핵심종목 156개가 수집기에서 누락(Yahoo엔 정상 존재, 39/40 확인) → `scripts/ops/sync_us_daily_quotes_and_factors.py --tickers ... --period 1mo`로 재수집, 9/23 종목수 3,424→3,538 복구.
  9/22는 100개 종목에 Yahoo 자체가 바를 주지 않음(소스 측 공백, 채울 수 없음). 재수집이 쓴 9/24 장중 34행은 삭제(미완성 봉).
- 수급 공백(2026-09-14~18): 다른 세션이 14:01에 `scripts/backfill_supply_from_kiwoom_20260924.py`로 금액 컬럼(*_net_buy_amt) 11,488행을 이미 백필함(검증 완료 확인). 남은 것은 **수량 컬럼**(inst/frn/ind_net_buy) ~2,335종목×5일 NULL —
  kiwoom_investor_daily에는 수량 컬럼이 없어 같은 방식으로 못 채움, KRX OpenAPI(collect_krx_investors.py)는 HTML 오류 페이지 반환(서비스/권한 문제)이라 사용 불가. 중복 작업 피해 미조치.
  (수집기 collect_krx_investors.py는 '오늘' 날짜만 처리하고, 가격행이 없으면 close=0 플레이스홀더를 INSERT하는데 write guard(close<=0 차단)에 걸림 - 기존 충돌, 미수정.)
- 미완: 피처 스냅샷(strategy_feature_snapshot[_pit_v2], 각 07-24/08-11에서 정지) 생성기가 저장소에 없음 → 재생성 스크립트 신규 작성 필요.

## Python 3.12 전환 준비·검증 (Claude, 2026-09-25) — 운영 전환은 미실행(승인 대기)
- 설치: Homebrew python@3.12.14(`/opt/homebrew/opt/python@3.12/bin/python3.12`). 새 venv `runtime/.venvs/py312`(numpy 2.2.6, pandas 2.3.3, pykrx 1.2.9, 운영 115패키지 = 3.11 freeze 기준; `.venvs/freeze_py311_20260924.txt`, `.venvs/requirements-py312-candidate.txt`, `.venvs/requirements-core.lock`).
  OpenDartReader: PyPI의 0.2.3 sdist는 Requires-Python>=3.13이라 pip가 거부 → 3.11 venv의 순수 파이썬 패키지 디렉터리(OpenDartReader + dist-info)를 복사해 사용(import 정상, 정규식 이스케이프 SyntaxWarning 1파일만).
- 검증(3.12): pytest 448 passed / 주요 모듈 import 전부 OK / TestClient로 API 8종 3.11 대비 동일(cash-conversion-signals/top은 정렬 비결정성 — 3.11끼리도 달라짐, 순서 무시 시 동일) / 지표(_calc_metrics·MDD)·가격 팩터 해시 동일 / db_compat numpy 스칼라 바인딩 동일.
  numpy2 차이: repr(np.float64)='np.float64(1.0)', uint8 산술 오버플로(NEP 50) — 코드에서 int8/uint8 사용 0건, `!r`는 문자열 값만이라 영향 없음.
- pykrx 1.2.9: 종목 OHLCV는 1.2.4와 동일값(175330 2022-02-04=8,400 = DB). ETF(get_etf_ohlcv_by_date)는 1.2.9에서 `'isin'` 오류, 1.2.4는 빈 결과 → 둘 다 사용 불가. 전종목 일괄(get_market_ohlcv_by_ticker)은 KRX 응답 형식 변경으로 실패. import 시 'KRX_ID/KRX_PW 없음' 안내만 출력(OHLCV엔 영향 없음).
- 전환 미실행 사유: 자동 분류기가 "운영 배포"로 차단(운영 venv 교체 + 백엔드 재시작). 절차(사용자 승인 후): `mv venv .venvs/py311 && ln -s .venvs/py312 venv && scripts/safe_restart_backend.sh`; 롤백: `rm venv && ln -s .venvs/py311 venv` 후 재시작. 실행 중 3.11 프로세스는 백엔드(uvicorn, launchd com.stock-dashboard.local)뿐이었음.
- 부수 발견·수리: price_history의 2026-09-14~23 일봉이 장마감 동시호가 전 스냅샷 값으로 저장돼 있었음(9/21 67% 불일치). KRX 공식(marcap 9/14~21, pykrx 9/22~23)으로 7,653행 교체(`scripts/fix_recent_close_from_official_20260924.py`, run_id recent_close_official_fix_20260925_002046). pykrx 호출을 ThreadPoolExecutor 안에서 돌리면 10분 이상 멈춤(순차 호출은 0.03초/건) → 순차로 변경.
  근본 원인(어떤 수집기가 마감 전 값을 최종으로 저장하는지)은 미확인 — 수집 시각/소스 점검 필요.
- 스냅샷 재구축: `scripts/build_strategy_research_dataset.py --adjust-jumps`(기업행위 분류 4종만 수익률 0 처리) 추가, 결과 테이블 strategy_feature_snapshot_rebuild_adj_20260924(원본 테이블 미변경). PER은 2026-06 이후 스냅샷에서 99.7% NULL(valuation_history가 2026-03-31에서 끝남).

### 스냅샷 조정 라벨 검증 결과 (Claude, 2026-09-25)
- 첫 `--adjust-jumps` 결과(3x_12m 양성 10,176→24,392)는 **내 버그**: 미래 경로는 조정 종가, 기준가는 스냅샷 시점 원본 종가 → 이후 역분할·감자 종목의 기준이 어긋남(추가 양성 14,729 중 창 안에 마스크 이벤트가 있는 것은 976건뿐). 기준가를 같은 조정 시리즈(`price_values[pos]`)로 수정해 재구축.
- 수정 후: 원본(raw) label_10x_24m 1,474(1.23%) → `_adj` 813(0.68%) / `_adj_legit` 980(0.82%); label_3x_12m 10,176(6.83%) → 8,275(5.55%) / 8,885(5.96%). 변경 행의 96%(2,181/2,269)가 12개월 창 안에 마스크 이벤트를 가짐.
  제거된 10x 라벨 720 중 505(70%)는 DART확정/발행주식수 근거가 있는 진짜 기업행위, 203(28%)은 근거 없는 pending 이벤트(82%가 상승 점프)만 있음 → 실제 급등일 수 있어 `--legit-only`(근거 있는 이벤트만 마스크)를 권장안으로 삼음.
  결론: raw 라벨은 역분할·감자로 10x 양성을 ~50% 과대계상. 권장 테이블 `strategy_feature_snapshot_rebuild_adj_legit_20260924`(원본 미변경).
- CEO 플랫폼(8011): 3.12 전환 완료 확인. 기존 500 원인 — `psutil` 미설치(3.11에도 없었음, 설치 후 monitoring-status/unified_metrics 200), `/health`는 응답모델 `Dict[str,str]`에 중첩 dict(`autonomous_state`) 반환 → ResponseValidationError(미수정, 파일 수정 중).

## ⚠️ PER 결함 발견·수정 — 이전 연구 결과 무효화 (Claude, 2026-09-25)
- **결함**: `valuation_history.per`는 1~3분기 행이 종가/분기EPS(연 환산 안 함), 4분기 행이 종가/연간EPS이고 분기말 종가로 고정돼 분기마다 정의가 다르고 시점 정합이 아님(삼성전자 2025년 PER 60→144→52, 실제 10~20대). 스냅샷 생성기가 이 값을 그대로 써서 `per`(및 PER 기반 model_score/heuristic)가 오염, 2026-06 이후는 99.7% NULL.
- **수정**: `scripts/build_strategy_research_dataset.py --ttm-valuation` — 스냅샷 시점마다 직전 4개 연속 분기(공시 시차 Q1~Q3 45일/Q4 90일 반영) 순이익 합으로 `per = 시가총액/TTM순이익`(≤0이면 NULL), `pbr = 종가/직전 공시 bps`. 결과 PER 채움률 최근 스냅샷 ~62%(이전 0.3%), 삼성 2025-03~11 = 9.9→20.4(타당).
  권장 테이블 **`strategy_feature_snapshot_rebuild_v3_20260924`**(`--adjust-jumps --legit-only --ttm-valuation`, 원본 테이블 미변경). `research/extract_research_inputs_20260924.py`도 이 테이블을 읽도록 변경.
- **영향(재계산 완료, md 정정)**: Alphalens 저PER IC 0.104→0.072(t 6.5→3.3, Q5-Q1 +3.7→+1.0%p), 저PBR 0.098→0.071. 저변동성은 견고(0.159→0.154). vectorbt/PyPortfolioOpt는 **종목 선정 자체가 달라져 이전 수치 무효**: 동일비중 CAGR 18.1%→7.0%, Sharpe 0.82→0.41, MDD -39%→-52%; 규칙 없음 훈련 CAGR 1.9%(KOSPI 5.7% 미만). 즉 "저PER+저변동성+신고가 20종목" 롱온리는 초과수익이 없음.
  마스크 대상도 4개 분류 전부→근거 있는 기업행위로 좁혀 마스크된 수익률 5,878→988건.
- 남은 한계: PER은 CFS 우선·분기 순이익(3개월) 합 기준이라 financial_data 품질(예: 2026Q2 삼성 total_equity 4.4조 이상치)에 의존, 지배주주 귀속 순이익이 아닌 전체 순이익 사용. PER 기반 `model_score`는 재학습됨(여전히 음의 IC).

## 📋 추가 계획 — 사용자 검토 대기 (Claude, 2026-09-25)
> 아래는 **제안**이며 아직 구현하지 않았습니다(프런트엔드·서버 재시작·운영 패키지 변경은 승인 후). 우선순위/범위를 알려주시면 진행합니다.

### A. 프런트엔드 반영 및 페이지 배치 검토
| 기능 | 배치 후보(기존 화면) | 필요한 것 | 규모 | 권고 |
|---|---|---|---|---|
| QuantStats 성과(Sortino, KOSPI 대비 알파·베타, CAGR, 곡선 출처 배지) | **전략센터 → 📊 성과 매트릭스**(StrategyHub.jsx, `/api/backtest/matrix`) 컬럼 추가 + HTML 티어시트 링크 | `GET /api/research/quantstats`(research_outputs/quantstats_summary_*.json 읽기), 곡선 출처(engine/mtm) 배지 | 소 | **권고 1순위** — 기존 표에 열 추가 |
| Alphalens 팩터 검증(IC/IR/분위수익) | **전략센터에 신규 탭 "🔬 팩터 검증"**(탭 배열에 1항목, `hubTab==='factor'`) | `GET /api/research/factor-validation`(csv/json), 팩터×기간 표+t값 색상, "PER 결함 정정" 배너 | 중 | 권고 2순위 |
| vectorbt 규칙 탐색·PyPortfolioOpt 결론 | **전략센터 → 🧪 검증 이력**(ExperimentLedgerPanel, `signal_experiment_ledger`)에 **실험 기록으로 등록**(별도 UI 불필요) | 원장 INSERT(기각/보류 결론+수치+근거 파일 경로) | 소 | 권고 — 이미 "기각이 정상"인 곳 |
| 가격 무결성(미해결 점프 146, coverage_gap_reviewed 524, 격리 8,396, 복구 run 목록) | **전략센터 → 🧭 데이터 라우팅** 탭 또는 **RiskGateMonitorView**에 "데이터 품질" 카드 | `GET /api/research/price-integrity`(price_jump_audit 분류 집계, fix_log 최근 run) | 중 | 권고(백테스트 신뢰도와 직결) |
| 기업행위 이벤트 951건·발행주식수 증거 | **종목 상세**(StockDecisionEvidencePanel) "기업행위" 타임라인 + 가격 차트 마커(문서 §7-6 Lightweight Charts 도입 시 함께) | `GET /api/stock/{code}/corporate-actions` | 중 | Lightweight Charts 도입과 묶어 진행 |
| 텐배거 라벨 정정(raw 10x 1.23% → 0.82%) | **TenbaggerProjectView**의 기준율 문구·임계값 | 문구/상수 갱신, 학습 데이터 v3 테이블로 교체 검토 | 소 | 모델 재학습 전 결정 필요 |
| 미국 가상매매 거래일 기준(핵심 유니버스) | **StrategyCenterView(미국 종이운용)** 준비상태 카드 | 이미 API가 `basis`, `reference_ticker_count` 반환 — 문구만 표시 | 소 | 서버 재시작 후 |
| Python/라이브러리 버전 | 관리자/시스템 정보(있다면) 또는 `/api/system/runtime` | 버전·venv 경로 표시 | 소 | 선택 |
- 프런트엔드 빌드/배포 절차(`frontend/dist`, 백엔드 재시작)는 운영 배포에 해당 — 반영 시점 합의 필요. 신규 라우터는 `routes/research_lab.py` 한 파일로 격리(파일 읽기 전용, DB 쓰기 없음) 제안.

### B. 라이브러리·환경 (docs/LIBRARY_INVENTORY_20260925.md)
1. **취약점 패치**(pip-audit): 운영 venv 14개 패키지(aiohttp 14건, starlette 5건, python-multipart 5건, cryptography 4건, soupsieve 4건 등). 제안: (a) 마이너/패치 묶음(aiohttp≥3.14.3, anyio, click, idna, lxml≥6.1, pyasn1, requests≥2.33, soupsieve≥2.9, urllib3≥2.7, curl-cffi≥0.15, anthropic≥0.87, python-multipart≥0.0.31)을 **새 venv `.venvs/py312b`에 적용→pytest 473+API 비교→전환**(이번 3.12 전환과 같은 절차), (b) starlette 1.x·cryptography 50은 FastAPI 호환/의존 확인 후 별도, (c) CEO 플랫폼 starlette 0.47.3(6건)도 동일.
2. 연구 venv: setuptools 83+ 업그레이드(간단), alphalens 로컬 패치 재적용 스크립트화.
3. OpenDartReader 복사 설치를 `requirements`에 명시(주석)하거나 PyPI 0.2.3 사용 가능 여부 확인.
4. vectorbt 라이선스(Commons Clause) 저장소에서 확인.
5. pykrx: KRX_ID/KRX_PW를 제공하면 1.2.9의 로그인 기반 기능이 열릴 수 있음(자격증명은 사용자가 직접 `.env`에 — 저는 입력하지 않음). ETF는 두 버전 모두 불가 → FDR/Naver 대체 유지.

### C. 데이터·수집기 (미해결)
1. **일봉 마감 전 스냅샷 원인 수집기 특정**: 9/14~23 일봉이 동시호가 전 값으로 저장(7,653행 교체함). 수집 시각/소스(`collect_naver_ohlcv_today` 등)·재발 방지(저장 전 KRX 공식값 재확인 또는 16:00 이후 확정 저장) 필요. 매일 자동 검증(marcap/pykrx 대비 종가 불일치율 알림) 크론 제안.
2. **7/12 SQLite 재적재 배치 원인 스크립트** 미특정(같은 초 223만행). 가드는 소수점만 차단 → 정수 배율 오류용 가드(전일 대비 가격제한폭 초과+공시 없음이면 격리) 검토.
3. 수급 수량 컬럼(inst/frn/ind_net_buy) 9/14~18 ~2,335종목 NULL — 소스 없음(금액은 다른 세션이 백필함). 수량이 필요한 곳(tenbagger_engine 등) 영향 조사 후 금액/종가 근사 여부 결정.
4. 미국 데이터: 9/22 핵심종목 100개는 Yahoo 자체 결측. 수집기 stale-only 로직이 중간 구멍(하루 누락)은 복구 못 함 → 일자 구멍 재수집 모드 제안. 핵심종목 98개가 9/22에 동시 결측된 원인은 미확인.
5. 미해결 점프 146건(DART 증거 없는 진짜 점프 ~60): 공시 조회 창/유형 확대 또는 수동 검토 목록 제공.
6. 소수점 8,396행(ETF 이전 구간, 원본 없음) 격리 유지 — 격리 행이 백테스트에서 실제로 제외되는지 회귀 테스트 필요.
7. 스냅샷: v3 테이블을 `strategy_feature_snapshot`로 승격할지(기존 소비자: tenbagger_engine, research 스크립트) 결정 — 승격 전 소비자 영향 점검 및 PIT(공시 시차) 확인. 스냅샷 월별 갱신 크론이 없음(07-24/08-11에서 멈춤).
8. 쓰기 가드 자동 테스트(Postgres 필요) — 테스트 DB 픽스처 도입 여부.

### D. 연구 후속
1. 종목 선정이 약함(TTM PER 기준 롱온리 20종목 CAGR 7%) → 다음 후보: 팩터 결합을 Alphalens IC 가중으로(고정 가중치 금지), 섹터 중립화, 거래비용 민감도, 하락장 포함 검증. 저변동성만 견고 → 저변동성+품질/수익성 결합 검증 제안.
2. 기존 전략 26개 성과의 KOSPI 대비 초과수익이 대부분 베타(0.3~0.9) 수준 — 엔진에서 **일별 평가곡선 저장**으로 QuantStats 재계산(현재 15개는 MTM 근사).
3. Alphalens를 전략 신호 단위로(44개 전략의 진입 신호 로그 필요) — 신호 로깅 스키마 제안 필요.
4. 텍스트 감성(FinGPT 대체=이미 설치된 API), Qlib은 Alpha158 정의·walk-forward 방식만 차용 — 우선순위 낮음.

### E. 알려진 미수정 버그
- CEO 플랫폼 `/health` 500: 응답모델 `Dict[str,str]`에 중첩 dict 반환(`main.py:784` 부근, 다른 세션이 파일 수정 중이라 미수정).
- 터미널에서 서버를 `&`로 띄울 때 `< /dev/null & disown` 없으면 tty output으로 정지(8011에서 발생).

## HANDOFF §10 "적용 후 검토" 처리 (Claude, 2026-09-25) — 상세는 docs/HANDOFF_GITHUB_ADOPTION_20260924.md §10-4
- **⛔ P0-1 스냅샷 정본 교체 차단**: 자동 권한 분류기가 정본 교체(공유 운영 테이블 변경)와 교체 스크립트 파일 작성을 차단 → 미실행. **v4**(`strategy_feature_snapshot_rebuild_v4_20260925`)를 준비하고 SQL 절차를 §10-4에 기록(사용자 실행/승인 대기). 이에 종속된 P0-2(valuation_history per_ttm)·P0-3(월간 재생성 잡)·aqr 재백테스트·소비처 점검은 미착수.
- **🔴 신규 발견 — 스냅샷 생존편향**: `security_master_history`의 폐지·합병 보통주 549개가 전부 `security_type='listed_equity'`인데 생성기 필터가 `'주권'/'common_or_unknown'`만 통과 → **폐지 종목이 스냅샷·라벨·팩터 연구에서 전부 누락**. 필터 수정(우선주 제외 유지) 후 v4: 190,609행/2,708종목(+205 폐지), 10x_24m 0.82%→0.91%. 연구 입력(`extract_research_inputs_20260924.py`)도 v4로 전환, 폐지 200종목이 수익률에 포함됨(팩터 결론은 거의 불변: 저변동성 견고).
- 연구 결과(정정): 학습/검증 분할에서 저변동성 IC 0.168→0.165, 저PER 0.070→0.114, 저PBR 0.077→0.082 유지, 모멘텀 -0.05→+0.01 반전(기간 특이), model_score -0.03→-0.15(악화), 수급 -0.06→-0.015(약화), small_size 부호 반전. 이벤트 스터디(공시 익일 진입·시장 중앙값 대비·윈저라이즈): 자사주 취득결정 +6.0%p(검증 t 5.3)·신탁체결 +5.6%p(t 8.9)·CB 발행 -2.6%p(t -4.5, 지속). 수주·특허는 평균이 꼬리 의존(중앙값≈0). 원장 13건 기록(`scripts/record_research_ledger_20260925.py`).
- P1-4: 모멘텀·돌파 대용 신호로 운영 규칙(KOSPI<MA60) on/off 비교(운영 `_tx_cost` 동일 비용, 2026-07~09 급락 창 포함) → 필터 ON이 전 구간 CAGR·MDD 개선(급락 창 MDD -41.6%→-23.7%), 유지. 대용 신호 자체는 손실(회전율 과다) — 최종 확정은 운영 전략 신호 로그로.
- P1-7/8: `backtest_equity.py`(엔진 곡선 또는 거래로그+가격 MTM 저장), PK `(run_id,source,date)`·뷰 `backtest_equity_curve_best_v` 적용 완료, QuantStats는 가정 자본 곡선 위험지표 제외.
- P2: `requirements/` 추적 경로·`-c core.lock`, OpenDartReader 설치 절차, 연구 venv 3.12 재생성(+`research/alphalens_compat.py`로 패치 제거, 재현 검증 diff 0), `research_venv→research_venv312`.
- P2-13: Lightweight Charts(`PriceChart.jsx`, App.jsx 분기, `chart_engine=svg` 롤백) — 브라우저 검증 완료, `dist` 배포 미실행. P2-14: 종가 공식 검증 잡(19:30)·KIS 일별수집 원장 편입 — 서버 재시작 후 반영. P2-15: CEO `/health` 수정(8011 재시작 후 반영).
- **서버 재시작이 필요한 반영분**(운영 배포로 분류돼 미실행): 스케줄러(종가공식검증·KIS 원장), `backtest_common._save_result` 곡선 저장, 프런트 `dist` 빌드, CEO 8011. 운영 백엔드는 `bash scripts/safe_restart_backend.sh`(CLAUDE.md 규칙), 프런트는 `cd frontend && npm run build` 후 재시작.

- (2026-09-25) P0-1 정본 교체 완료(사용자 실행). P0-3 월간 재생성 잡은 스크립트 작성 차단 → 사용자 승인/직접 작성 필요, P0-2(`valuation_history` TTM)·aqr_multifactor 재백테스트 대기.

- (2026-09-25) aqr 재백테스트 완료: 구→신 수익률 +98→+55, -30→-20, -9.5→-1.7, +31→+5, +13→+20, +6.7→+9.1 (look-ahead PER 제거). 월간 스냅샷 스크립트 검증 완료, 스케줄러 등록·P0-2(valuation_history TTM)만 남음.

- (2026-09-25) P0-2 완료: valuation_history.per_ttm/ttm_net_income 추가(중앙값 PER 약 10~12배, 삼성 9~15배로 안정). 남은 항목: 스케줄러에 월간피처스냅샷 등록(편집 권한 대기).

- (2026-09-25) 계획 A 1차 구현: 팩터 검증 탭 + /api/research/* 라우터(읽기 전용). 빌드·API 단위 확인 완료, 브라우저 렌더 확인은 재시작 후 필요. 남음: QuantStats 열(성과 매트릭스), 가격 무결성 카드, 종목 상세 기업행위 마커.

- (2026-09-25 밤, S5) **"추가 계획 A"와 실제 구현 범위 정정**: 계획 A 1차가 이미 구현·배포돼 있다 — `routes/research_lab.py`(`/api/research/factor-validation`·`quantstats`·`price-integrity`, 읽기 전용), `FactorValidationPanel.jsx`(팩터 IC·이벤트 스터디·QuantStats 표), `PriceIntegrityCard.jsx`(데이터 라우팅 탭 상단). 미구현: 성과 매트릭스 표에 QuantStats 열, 종목 상세 기업행위 타임라인, 텐배거 문구, 시스템 런타임 표시. **검토 전 노출 차단**: 탭은 `localStorage.research_lab_tab='1'`일 때만 표시(가림), `/api/research/*`는 터널 경유 시 `API_WRITE_TOKEN` 필요(`security_gate.py`). 검토 후 플래그·게이트 제거.

- (2026-09-25 밤) §12 R2~R9·shadow A안 처리 완료 — HANDOFF §14. 재시작·Cloudflare Access 재확인·py312b 전환·R3 플래그·momentum/peak 원장 수정 범위가 사용자 결정 대기.

- (2026-09-26) §15 V1~V9 처리 완료 — HANDOFF §16. 남은 결정: Cloudflare Access 앱 확인·`CF_ACCESS_*` 설정, CEO 8011 위험 쓰기 엔드포인트 세션 인증(다른 세션)·`.venv312b` 전환, V7(`v_gc` shadow·LAN 제한).

## Codex 최신 문서·운영 상태 재검토 및 보완 (2026-09-26 12:48 KST)

`runtime/AGENTS.md`, 루트/런타임 `CLAUDE.md`, 이 파일의 최근 이력과 운영 PostgreSQL을 대조했다. 루트는 공용 데이터 경로이고 실제 코드·git 정본은 `runtime/`이며, 단수 `AGENT.md`는 없고 `runtime/AGENTS.md`가 현재 규칙 정본이다. Claude의 FnGuide 분기 스냅샷 수집(`scripts/collect_fnguide_quarterly_snapshot_20260926.py`)은 이 점검 중에도 별도 프로세스로 실행 중이므로 해당 파일·수집 행은 수정하지 않았다. `fill_suspension_gaps_from_marcap_20260924.py --apply` 금지와 기존 57,874행 자동 롤백 금지도 그대로 유지한다.

### 새로 발견해 수정한 사각지대

1. **기본 pytest가 실험 스크립트를 실행하는 문제**: pytest 설정이 없어 저장소 루트에서 `pytest`를 실행하면 `scratch/`의 `*test*.py` 63개와 루트 `test_interp.py`까지 수집했다. 이 파일들은 import 단계에서 운영 PostgreSQL 연결·백테스트 실행·없는 로컬 파일 접근을 수행하므로, 단순 회귀검사가 운영 작업을 건드릴 수 있었다. `pytest.ini`에 `testpaths=tests`, `python_files=test_*.py`를 추가해 기본 명령이 정식 테스트만 수집하도록 고정했다.
2. **전략 스위트 교체 후 검증 아티팩트 누락**: 2026-09-25 가격 기준 재실행으로 선택 run hash가 바뀌었지만 새 hash에는 `survivorship_integrity`와 `corporate_action_integrity`가 재등록되지 않았다. 이 때문에 실제 가격 감사 통과 여부와 무관하게 27개 선택 전략 중 13개가 `legacy`였고, v4 최신 선택 스위트 `3a1df776883808d8`도 문서 설명과 달리 `legacy`였다. `audit_selected_strategy_price_integrity.py`를 현재 정본 뷰로 재실행해 27개 전부의 아티팩트를 다시 만들었고, v4는 **`point_in_time_verified`로 복구**됐다(남은 미통과 사유는 `forward_validation`뿐).
3. **재발 방지 배선**: `scripts/rerun_selected_after_price_repair.py`와 `scripts/rerun_all_after_audit_rebuild.py`가 새 스위트 선택을 마친 뒤 전체 선택 전략 무결성 감사를 반드시 실행하고, 결과 요약(`checked_at`, 정책 버전, 통과/실패/무거래 수)을 재실행 결과 JSON에 저장하도록 수정했다. 향후 가격 복구·감사 재빌드 뒤 새 run hash가 검증 행 없이 남는 문제를 막는다.

### 운영 DB 실측 스냅샷

- **가격 감사(12:43 KST)**: `unresolved_active_common` 64건, `quarantined_basis` 8,448건, `coverage_gap_reviewed` 524건, 미검토 `coverage_gap` 1건이다. 따라서 이 파일 앞부분의 “미해결 146 / 소수점 8,396”은 과거 시점 수치이며 현재값으로 사용하면 안 된다. 정본 뷰의 `return_usable=0`에는 거래정지 파생행 263,458건도 별도로 포함된다.
- **선택 전략 가격 무결성 재감사(12:47 KST)**: 27개 중 23개 통과, 4개 실패. 실패는 `deep_recovery`(가격오염 9.34% + survivorship 2), `extreme_dd_volume`(12.63%), `low_base_breakout`(3.64% + survivorship 5), `v12`(0.34% + survivorship 1)다. 5%·7% 민감도 모두 23개 통과라 현재 결과는 7% 경계에 의존하지 않는다. **이 23/4는 가격·상장구간 감사 결과**이며 전략 전체 등급과 동일한 숫자가 아니다. 전체 게이트 기준 현재 분포는 `point_in_time_verified` 1(v4), `point_in_time_approx` 14, `execution_strict` 3, `legacy` 9다.
- **재무 검증(12:43 KST)**: `QUARTERLY_4WAY`는 OPEN 4,270, AMBIGUOUS 15, CONFIRMED 52,840이다. `OFS_ANNUAL_CONSISTENCY` OPEN 13,231·AMBIGUOUS 1,215는 여전히 후속 분류 대상이다.
- **FnGuide 진행 중 스냅샷(12:43 KST)**: 2026Q1·Q2 각각 188종목이 `financial_source_snapshot(data_source='fnguide')`에 수집됐다. 실행 중인 장기 수집이므로 완료 수치로 간주하지 않는다.
- **피처 스냅샷**: 정본 `strategy_feature_snapshot`은 190,609행, 최대 기준일 2026-09-23으로 확인됐다.

### 검증

- Python 3.12 운영 venv에서 수정 파일 `py_compile` 통과.
- `venv/bin/python -m pytest -q`가 이제 정식 스위트만 실행하며 **543 passed, 54 subtests passed**(경고 2건: Pydantic v1 validator, Starlette TestClient cookies deprecation).
- 가격 원시행·재무 원시행은 이번 Codex 점검에서 쓰지 않았다. 운영 DB 쓰기는 현재 선택 run hash에 대한 검증 아티팩트 갱신뿐이다.

### 현재 판단

v4의 과거 데이터/PIT 문제는 최신 선택 스위트 기준으로 다시 검증 완료됐지만 시스템 전체가 완료된 것은 아니다. 가격 감사 실패 4개 전략, 재무 OPEN/AMBIGUOUS 플래그, 진행 중인 FnGuide 전수 수집, 그리고 `forward_validation`은 계속 남아 있다. Claude의 동시 작업 결과는 완료 시 이 스냅샷 이후 수치로 다시 갱신해야 한다.

## Codex 복합전략 600%대 유효성 재점검 (2026-09-26 14시대 KST)

### 판정

전략센터의 600%대 수익률은 **현재 유효 성과가 아니다**. 화면 최상단에 노출되던
`cmb_da1d39936923` 646.43%는 300720의 2021-09-09 오염 종가 238,000원(정상
가격대 약 23,800원)을 이용해 한 거래가 +1,451%로 청산된 결과다. 이 거래는 전체
손익의 21.6%를 만들었고, 복리로 이후 티켓까지 키웠다. 300720을 제외한 동일 주문
재시뮬레이션은 371.2%였다. 따라서 646.43%는 참고값으로도 사용하지 않는다.

그보다 앞선 688.94%도 2026-09-08 수정 코드 재현에서 403.09%로 내려갔고, 8회
동점순서 안정성은 평균 368.64%·중앙값 366.33%·범위 331.61~404.78%였다. 이
366~403% 역시 2026-09-08 재무 스냅샷 기준의 과거 결과다. 2026-09-26 FnGuide
기준 재무 재정리가 끝나기 전에는 최신 유효값이 없다.

### 이번 수정

- 원천 composite run hash `83e9a856a4ce`와 병합 run hash
  `9201c784855773c5`에 `result_validity=false` 아티팩트를 기록했다. 저장 행은 감사
  추적을 위해 보존한다.
- `run_registry.derive_status()`에 `result_validity` 게이트를 추가했다. 사후 감사에서
  손익이 무효화된 컴포넌트는 `execution_strict` 이상을 잃고 새 병합계좌 등록에 다시
  사용될 수 없다.
- `/api/backtest/combinations/list`는 무효화 run을 제외한다. 각 run의 저장 당시
  `financial_data` 수정시각과 현재 수정시각을 비교하고, 최신
  `cfs_ofs_mixed_ttm` 계약이 fail이면 `validation_status=revalidation_required`를
  반환한다.
- 전략센터 화면은 재검증 중 수익률 숫자와 `현재 최고` 배지를 숨기고 `재검증 대기`로
  표시한다. `routes/trend.py`의 552/577/530/487/585% 고정 라벨도 제거했다.

### 현재 데이터 계약과 후속 순서

2026-09-26 13:13 KST 계약 기록은 `cfs_ofs_mixed_ttm` fail 3,022/8,121행이다.
`financial_data`는 같은 날 23,281행이 갱신됐고, 34차의 `dart_q_full_fetch.py` 3개
프로세스가 아직 실행 중이므로 TTM PER 재계산과 전략 재실행을 지금 하면 다시 낡는다.

34차 완료를 다음 조건으로 확인한 뒤 순서대로 진행한다.

1. 분기 2016~2025Q1 지배주주 순이익·자본 적용과 Q4 재계산 완료
2. `add_valuation_history_per_ttm_20260925.py --apply`
3. `data_contract_audit_20260925.py`에서 `cfs_ofs_mixed_ttm=ok`
4. 9/30 월간 `strategy_feature_snapshot`이 새 TTM 값을 쓰는지 확인
5. PER 기반 Alphalens와 병합전략 컴포넌트를 동일 데이터 지문으로 재실행
6. 가격오염 보유구간은 비율 7%만 보지 않고 손익기여까지 검사한 뒤 새 병합 run 등록

검증: 신규 `result_validity` 회귀테스트 1건 통과, Python 문법 검사 통과, 프론트엔드
프로덕션 빌드 통과. 운영 API 직접 호출에서 646.43% run 제외와 남은 4개 run의
`revalidation_required` 전환을 확인했다.

## Codex 선택 전략 감사 재검증·정정 (2026-09-26 13:35 KST)

바로 위 12:48 스냅샷의 **23통과/4실패 판정은 감사기 결함 수정 전 결과이므로 현재 판정으로 사용하지 않는다.** 선택 전략 실패 4개와 v4 `forward_validation`을 다시 추적해 다음을 확인·수정했다.

1. **중복 보유창 결함 수정**: 일부 레거시 거래원장은 같은 거래를 `BUY` 이벤트 행과 완결 거래 행으로 함께 저장한다. `holding_windows()`가 완결 행을 정상 창으로 추가하고도 원래 BUY를 열린 포지션으로 남겨 기간 말까지 두 번째 가상 창을 만들었다. 예: deep_recovery 011690은 2020-03-17 손절 완료인데 2021-11-30까지 보유한 것으로 중복되어 이후 거래정지 419일이 오염으로 붙었다. 완결 행의 `(종목, 진입일)`과 같은 BUY 이벤트를 다시 열지 않도록 수정하고 회귀 테스트를 추가했다. 수정 직후 `extreme_dd_volume`은 실패에서 통과로 바뀌고 결과는 24통과/3실패가 됐다.
2. **KONEX 이력 사각지대 수정**: 일별 KRX KOSPI/KOSDAQ 정본에 없다는 이유로 이전 구간을 일괄 `pre_official_equity_reference_ineligible`로 만든 정책이 실제 KONEX→KOSDAQ 이전상장 종목도 제외했다. KRX KIND 공시로 126340 비나텍(2013-07-01 KONEX→2020-09-23 KOSDAQ), 107640 한중엔시에스(2013-12-10 KONEX→2024-06-24 KOSDAQ)를 확인했다. `security_master.py`에 증거 기반 KONEX 예외를 추가해 재빌드 후에도 유지되게 했고, 운영 DB는 `security_master_history_backup_codex_20260926_konex`(적용 전 4행) 백업 후 두 구간을 `official_disclosure_verified`/tradable로 반영했다.
3. **SPAC 과거구간은 계속 차단**: low_base_breakout의 127980·162300·380540은 현재 회사 상장 전 같은 코드의 합병 SPAC 가격 구간이다. 현재 회사명·재무를 그 과거 가격과 연결하면 identity look-ahead가 되므로 KONEX 예외에 포함하지 않았다. 이 전략의 survivorship 5건은 실제 결함으로 유지한다.

최종 재감사 결과는 **27개 중 26개 통과, 1개 실패**다. 유일한 실패는 `low_base_breakout`(220창 중 가격오염 3.64%, survivorship 5건)이다. 5%와 7% 임계값에서 모두 26개가 통과하므로 임계값 경계 의존성은 없다. `deep_recovery`, `extreme_dd_volume`, `v12`는 통과로 정정됐다.

### v4 재판정

- 최신 선택 스위트는 `3a1df776883808d8`, 방법론 상태는 `point_in_time_verified`, 미충족 방법론 게이트는 `forward_validation` 하나가 맞다.
- 다만 이것은 **운영 채택 직전**이라는 뜻이 아니다. 최신 실적은 +27.35/0/+2.97/+8.80/-16.32/+38.62, 평균 **+10.24%**, 4/6 양수이며 전략 거버넌스는 `retired`(자동매매 불가)다. 기존 화면 설명의 +19.46%, 2/6 양수, “전 사이클 최우수”는 이전 스위트 수치라 `routes/backtest.py`를 현재값과 retired 상태로 정정했다.
- v4 독립 prospective 원장(`sc_v4`)은 없고 전략센터 상위 5개에도 현재 포함되지 않는다. 따라서 `forward_validation`은 시간이 지나면 자동 완료되는 상태가 아니다. retired 전략을 우회해 새 paper 계좌를 자동 개설하지 않았으며, 향후 거버넌스 재승격 또는 명시적 연구 shadow 결정이 있을 때부터 60일·완결거래 20건 기준으로 새 표본을 모아야 한다.

검증: 신규/수정 단위테스트 11개 통과, KONEX 적용 후 선택 전략 전체 감사 재실행 완료. 가격·재무 원시행은 변경하지 않았고 보안 마스터 2구간과 선택 run 검증 아티팩트만 갱신했다.

## Codex 한국 주식 다중 분류 전면 개편 (2026-09-26 14:10 KST)

기존 분류는 `stock_universe.sector_*`와 `stock_sector_tags.sector`에 업종·테마·규칙
추론이 평면적으로 섞여 있었고, 출처 간 충돌을 판단할 수 없었다. 실측상 스탁이지
2026-09-23 원천 자체가 제이엠티·한국컴퓨터를 `인터넷/플랫폼`, 디케이티를 `SW/AI`로
분류하고 있어 기존 값을 정답으로 덮어쓰면 사업 실체가 훼손된다. 기존 HTML 정규식
수집기도 사이트 개편 뒤 0건을 반환하고 있었다.

### 구현

- `sector_taxonomy_nodes`, `stock_sector_membership_v2`,
  `sector_taxonomy_source_runs`를 PostgreSQL에 신설했다. 업종·시장구분·테마·밸류체인·
  공정·소재·실질 경쟁군을 독립 축으로 저장하고, 종목당 복수 태그와 부모 계층,
  제공자 원천코드, 스냅샷 날짜, 신뢰도, 상태(`verified/observed/inferred/legacy`), JSON
  근거를 보존한다. 조회는 출처별 최신 **성공** 스냅샷만 사용하며 과거 스냅샷은 남긴다.
- `scripts/rebuild_sector_taxonomy.py`를 추가했다. 스탁이지 공개 valuation API,
  키움 공식 `ka10101→ka20002` 업종 구성, `ka90001→ka90002` 테마 구성을 수집하고
  DART `company_product_mix`로 내부 반도체 밸류체인/공정/소재 후보를 만든다. 응답 중복은
  멱등 제거하고 소스별로 커밋한다. 한 소스가 실패해도 기존 성공 스냅샷은 유지하며
  실패 run을 별도 기록하고 프로세스는 실패 종료한다.
- 깨진 `sync_stockeasy_stock_analysis_sectors.py`를 HTML 정규식에서 실제 프론트가 쓰는
  JSON API로 교체했다. 2,000종목 미만이면 저장 전 실패시키며, 레거시 소비 테이블도
  2026-09-23 스냅샷 2,553종목으로 갱신했다.
- `scheduler.py`에 `종목다중분류` 잡을 매일 20:10으로 등록했다. 장시간 외부 API 호출
  동안 전역 DB 쓰기 잠금을 잡지 않으며, subprocess 실패는 수집 원장 실패로 남는다.
- `/api/sector-taxonomy/{overview,tree,stocks,stock/{code},peer-groups}`를 추가하고
  프론트 메뉴 `종목 다중분류`를 신설했다. 분류 트리, 구성종목, 종목별 모든 출처·근거,
  출처 불일치, DART 제품 매출, 경쟁군을 한 화면에서 조회한다. 원천 스냅샷이 30일을
  넘으면 화면에서 경고색으로 표시한다.
- 상세 기준과 소스 계약은 `docs/sector_taxonomy_20260926.md`에 기록했다.

### 운영 적재 결과

- 일반주식 유니버스 2,793 / 새 체계 커버리지 2,793(100%).
- 기존 기준선 7,484 관계(157노드), 스탁이지 5,106(62노드), 키움 테마 897
  (142노드), 키움 업종·시장구분 11,572(65노드), 내부 정밀분류 310(12노드).
- 키움의 실제 업종과 `KOSDAQ SMALL`·벤처기업 같은 시장 세그먼트를 분리했다.
- 제이엠티(094970)·디케이티(290550)·한국컴퓨터(054040)는 모두 검증 상태로
  `전자부품 → EMS·모듈 조립 → PBA·FPBA·모듈 조립 → OLED·스마트기기 PBA/FPBA
  제조사`에 연결했다. 근거는 2025 DART 제품 매출(PBA/FPBA, 스마트폰·전장,
  OLED-PBA·QD/LCD 모듈)이며 스탁이지의 상충 분류는 삭제하지 않고 불일치로 표시한다.

### 검증·반영

- 신규 저장계층 테스트 3개와 전체 정식 스위트 **551 passed, 54 subtests passed**.
- Python 문법 검사, 프론트 production build 통과.
- 운영 API의 overview/094970 상세/peer-groups 200 응답과 근거 내용을 확인했다.
- 자동 API 문서 476개/55그룹, DB 문서 310개 정식 테이블로 재생성했다.
- `safe_restart_backend.sh`로 PID 9818→21069, 고아 프로세스 없음, HTTP 200.

## Codex 미국 전용 백테스트 엔진 (2026-09-26)

한국 시장 전제의 `backtest_common.py`를 재사용하지 않고 `us_backtest_common.py`를
신설했다. 운영 PostgreSQL 실측은 `us_price_history` 4,045,930행·3,670 ticker,
2021-05-24~2026-09-25이며 `adj_close` 컬럼은 없다. 실제 수집 경로가 yfinance
`auto_adjust=True`라 OHLC 전체가 분할·배당 보정 기준이고, NVDA 2024-06 10:1 분할
경계도 연속 가격으로 확인했다. 미국 재무 78,630행은 SEC 실제 접수 기반
`avail_date`가 모두 채워져 있으며 엔진은 `avail_date<=signal_date`만 공개한다.

엔진은 D일 종가 신호를 다음 미국 시장 세션 시가에만 체결한다. 같은 날 종가 체결,
시가 결측 시 종가 대체, 가격 급변만으로 분할 추정은 금지했다. 목표 비중 재조정,
USD 현금·주당/최소/매도 수수료·슬리피지, 일별 MTM, 데이터 지문, 대형 가격 단절,
미체결·종료 미청산 품질 플래그와 SPY 벤치마크 비교를 구현했다. 현재 원장에는 SPY가
없어 기준 실행은 `benchmark_available=false`로 표시되고 초과수익을 산출하지 않는다.
저장을 요청한 실행만 한국 원장과 분리된 `us_backtest_runs/trades/equity`에 기록한다.

GitHub의 LEAN·bt·vectorbt·backtesting.py·QuantStats를 검토했다. LEAN은 가장
완전하지만 별도 C#/Docker 데이터 계층이 필요하고, 나머지도 과거 구성종목·상폐
수익·SEC 가능일을 자동 해결하지 않는다. 따라서 현재 의존성을 늘리지 않고 이들의
이벤트 순서·비용·성과곡선 원칙만 반영했다. 세부 비교와 사용법은
`docs/us_backtest_engine_20260926.md`에 기록했다.

기준 모멘텀 전략을 2022-01-03~2026-09-25 현재 S&P 500 전체로 읽기 전용 실행해
1,187세션과 65만여 유효 OHLC 행을 처리했다. 실행 자체는 완료됐지만 현재 구성종목을
과거에 소급한 결과이므로 `survivorship_bias=true`, 종료 보유 5종목으로
`execution_complete=false`, `research_grade=false`가 정확히 표시된다. 이 성과는
전략 유효성 근거로 사용하지 않는다. 연구 등급의 다음 필수 데이터는 날짜별 지수
구성 이력, 상장폐지·합병 최종 수익, ticker 변경 이력이다.

검증은 전용 테스트 10개와 전체 정식 스위트 **561 passed, 54 subtests passed**다.
운영 가격·재무 원장은 수정하지 않았고 전체 기준 실행도 읽기 전용으로 수행했다.
