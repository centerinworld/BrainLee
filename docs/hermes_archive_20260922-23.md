# hermes.md 아카이브 — 2026-09-22~23 절 (2026-10-05 이관, 2주 보관 규칙)

> 원문 그대로 이동. 현재 작업 현황은 hermes.md, 숫자 데이터는 docs/FINANCIAL_STATEMENTS.md.

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
> 📦 숫자 데이터 기록 → 이관: [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) 부록 B

## 000670(SK하이닉스) 미스터리 완전 해결 + 실제 수정 완료 (Claude, 2026-09-23)
> 📦 숫자 데이터 기록 → 이관: [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) 부록 B

## 035720(카카오) 동일 패턴 확인·수정 + 256940/300720 최종 판정 (Claude, 2026-09-23)
> 📦 숫자 데이터 기록 → 이관: [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) 부록 B

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
> 📦 숫자 데이터 기록 → 이관: [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) 부록 B

## ✅ 위 사고 실제 수정 완료 — 297만행 중 218만행 복구, 나머지는 지수/미커버 확인 (Claude, 2026-09-23)
> 📦 숫자 데이터 기록 → 이관: [docs/FINANCIAL_STATEMENTS.md](docs/FINANCIAL_STATEMENTS.md) 부록 B

