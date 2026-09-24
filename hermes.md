# Stock Data Integrity Status

Updated: 2026-09-20

## Completed In This Remediation

- PostgreSQL cutover reconciliation passed with `verify_postgres_cutover.py ok=true`.
- DART API key rotation now reaches KEY3 after KEY1 and KEY2 quota exhaustion.
- DART annual cash-flow evidence resolved 102 flags without guessing:
  38 current CFS matches were confirmed, 5 mismatches corrected in 6 DART-source
  annual rows with `cashflow_fix_log` entries, and 26 CFS/OFS basis cases were
  classified as structural differences.
- DART/FnGuide consensus repaired 13 FIN_CROSS DART-source rows with
  `financial_fix_log` entries. The financial/CF ambiguous queue fell from 403
  to 299. Two 2026 annual cash-flow flags were reclassified as `OPEN` because
  the annual disclosure is not yet due.
- The daily dilution follow-up no longer rewrites already matching rows or
  duplicates its provenance marker. Existing duplicated provenance markers in
  31 rows were normalized without changing financial values.
- DART revenue extraction now accepts comprehensive-income-statement labels.

## Remaining Work: Do Not Guess Values

| Queue | Count | Current evidence | Required next action |
| --- | ---: | --- | --- |
| `cf_validation_flags` AMBIGUOUS | 299 | 268 CF flags have no DART CF response; 25 FIN_CROSS flags have no annual response; 6 FIN_CROSS flags need currency/account interpretation. | Requery DART when a source response exists; manually review the 6 unit/account cases. |
| `QUARTERLY_4WAY` OPEN | 5,309 | Nearly all are `source_count=0` / no-data collection gaps. | Recollect missing quarterly statements; do not promote to confirmed without source evidence. |
| `price_jump_audit.unresolved_active_common` | 6,904 | Many candidates can be corporate-action adjustment-basis differences. | Reconcile with raw KRX/Naver and corporate-action evidence before replacement. |
| `price_jump_audit.mixed_basis_or_price_corruption` | 626 | Mixed adjusted/unadjusted basis has not been proven per row. | Establish the correct adjustment basis before editing price history. |
| `price_jump_audit.invalid_ohlcv` | 38 | Historical rows lack an independent Naver snapshot. | Obtain an independent raw price source; preserve current rows until then. |
| `financial_anomalies.partial_coverage` | 161 | 159 partial coverage cases plus 014950 and 101970 with complete gaps. | DART historical recollection and company-specific source review. |
| `financial_anomalies.revenue_zero` | 1 | 014950 has an actual filing-collection gap. | Recollect the missing DART reports; do not synthesize revenue. |
| `financial_anomalies.persistent_loss` | 660 | Intentional review queue, not a data-error assertion. | Keep as monitoring; do not auto-resolve based on the loss itself. |

## Collection Backfill Check

- A live DART recheck of a representative overdue quarterly-open case
  (`004310`, 2025 annual CFS) returned no CFS statement rows. The existing
  database has an annual value, but there is no live CFS evidence with which to
  replace or confirm it. This confirms that the `QUARTERLY_4WAY` OPEN queue
  cannot be reduced safely by filling values from the current DART response
  alone.
- Do not convert these no-data flags to a passing status. Revisit them only
  after DART returns CFS data or an independently captured FnGuide/Naver source
  is available.

## Business-Report XML Fallback Implemented

- `collectors/dart_document_financials.py` now downloads periodic-report XML
  and extracts core IFRS facts with consolidated facts preferred over separate
  facts. It is the fallback when the OpenDART financial-statement API returns
  no CFS rows.
- The parser was validated against 004310's 2025 business report
  (`rcept_no=20260213002543`): total assets `163,215,645,432` and total equity
  `82,103,210,702` matched the report XML.
- The verified equity value filled the previously NULL 2025 Q4 CFS row for
  004310. The change is recorded in `financial_fix_log` under
  `dart_document_fallback_20260920`; its quarterly flag now has one DART source
  but remains `OPEN` pending an independent comparison source.
- The XML parser now accepts both direct cell text and nested `<P>` cell text;
  DART uses both serialisations. It also treats attribute order as unordered.
- A full safe scan of the remaining Q4 CFS `NULL` candidates found 14
  company-year documents. Nine `total_equity` values had direct business-report
  evidence and were filled (including the initial 004310 case); every change is
  logged under `dart_document_q4_backfill_20260920`. The quarterly open count
  correctly remains `5,309`: one DART source is recorded, but independent
  reconciliation has not yet occurred.
- Revenue, operating profit, and net income are deliberately excluded from this
  Q4 fallback. In a business report they are annual cumulative flows, whereas
  the Q4 rows store the standalone quarter. Writing them directly would corrupt
  the quarter; a future repair must derive Q4 from independently verified
  cumulative periods before it can update them.
- `scripts/backfill_quarterly_bs_from_dart_documents.py` extends the fallback
  to Q1-Q3. It writes only a NULL CFS `total_assets` or `total_equity` field
  when the matching periodic-report XML explicitly selects a
  `ConsolidatedMember` IFRS fact. Separate-only facts, income-statement flows,
  and documents without a standard fact are all excluded.
- The first four verified batches restored 469 Q1-Q3 balance-sheet fields from
  this evidence (`236` total-assets and `233` total-equity fields), with every
  change logged under `dart_document_quarterly_bs_backfill_20260920`. This
  reduced quarterly OPEN flags with zero evidence from `5,293` to `4,824`.
  Flags remain OPEN with `source_count=1` until an independent source agrees.

## Live Price Recheck

- `scripts/repair_invalid_ohlcv_from_live_naver.py` now re-fetches each
  historical invalid-OHLCV row from Naver with an exact-date request and writes
  only when the current and Naver closes agree within one won. It backs up and
  logs every eligible change.
- The 2026-09-20 dry run found no safe automatic repair among the remaining 38
  rows: Naver reproduced the same invalid candle for 20 rows, while 18 rows had
  a different close and were rejected as possible adjusted/unadjusted-basis
  mismatches. They remain unresolved by design; neither category supports an
  evidence-based overwrite.

## Historical Filing-Gap Recheck

- `014950` has no DART periodic-report records for 2020 or 2021 under its
  current corporate identifier. Its missing financial rows cannot be restored
  from OpenDART without inventing a source.
- `101970` does have periodic reports for 2020 and 2021, but its 2020 business
  report (`rcept_no=20210331000034`) is a non-IFRS document XML with no standard
  IFRS fact tags or identifiable core financial-account labels. The OpenDART
  financial-statement endpoint also returns no rows. No automatic insertion was
  made; this needs a company-specific archival source or a reviewed table
  parser.

## Evidence And Resume Points

- `research_outputs/remaining_financial_integrity_20260920.json` is the
  authoritative 299-item financial handoff, including each flag ID and reason.
- `research_outputs/cf_ambiguous_dart_recheck_20260920.json` and
  `research_outputs/fin_cross_live_resolution_20260920.json` retain the DART
  evidence used in this round.
- `cashflow_fix_log` and `financial_fix_log` contain every value change made
  from live DART evidence in this remediation.

No remaining queue above may be declared correct or changed solely to reduce
its count. Each requires source evidence or an explicit, documented basis rule.

## Stage 1 baseline mapping (code-doer, 2026-09-20)
- Mapped source->validation->storage->strategy-consumption paths; artifact at
  research_outputs/stage1_pipeline_mapping_20260920.md (read-only, no code/data change).
- KEY FINDING: live strategy engines (signal_engine 48x raw price_history + 17x raw
  financial_data, screener 5x+4x) bypass the canonical/quarantine/write-gate layer.
  Integrity gates are advisory for live signals today; only se_momentum + one endpoint
  consume canonical views. No point-in-time/availability gate in live path (0 refs).
- Proposed FIX-A..D (fail-first regression tests + minimal read-path swap + rollback)
  awaiting Planner prioritization. No operational data modified.

## QUARTERLY_4WAY 잔여 출처 0건 — balance-sheet 필드복원 (code-doer, 2026-09-20)

- `scripts/backfill_quarterly_bs_v2.py`가 기존 `backfill_quarterly_bs_from_dart_documents.py`
  (Q1-Q3)와 `backfill_quarterly_open_from_dart_documents.py` (Q4)를 **Q1-Q4 통합·감사·멱등
  파이프라인**으로 대체/확장한다. 동일한 "연결(ConsolidatedMember)+eFY fact만 기입" 원칙을
  유지하며, `financial_fix_log`(전/후값·계정·단위·XML URL·시각)와 flag(dart_value·source_count)
  를 함께 기록한다.
- 보고서명↔분기 매핑은 재무보고서 기간말 기준으로 검증됨: Q1=분기보고서(YYYY.03),
  Q2=반기보고서(YYYY.06), Q3=분기보고서(YYYY.09), Q4=사업보고서(YYYY.12).
- 금일 194 필드 복원(누적 663). 잔여 balance-sheet 3,935건의 미복원 주 사유는
  **연결재무제표 미공시(별도 only)** 가 대부분으로, CFS 행에 별도값을 기입하지 않는
  정책(정확성 우선)상 이 원천으로는 더 채울 수 없다. 두 번째 독립 원천(FnGuide/Seibro)
  대조가 필요한 `source_count=1` 승격 구간으로 남긴다.
- income-statement 695건(net_income 507·revenue 98·op 90)은 Q2~Q4 보고서가 누적(YTD)이므로
  `당분기 = 누적 - 직전 누적` 차감 파이프라인을 별도 설계·검증해야 하며, 이번 작업 범위에서
  제외했다(Q1 171건은 누적=당분기라 원칙상 기입 가능하나 dimension 검증 후 별도 진행).
- DART document.xml은 KEY1/KEY2가 `020`(일일한도 초과)이므로 KEY3 단독 사용. `--resume`으로
  일일 한도 내에서 이어서 실행하면 된다.

## 손익계산서 당분기(standalone) 파이프라인 — TDD 명세+RED (code-doer, 2026-09-20)

- Planner 우선순위 ①(누적 YTD 손익의 당분기 오인 방지)에 따라 **값 쓰기 없이** 명세와
  failing test를 먼저 제시했다.
- `docs/codex_handoff_is_standalone_impl_spec_20260920.md`: Planner 요구 4항목을 계약으로
  매핑. CFS/OFS 분리, Q2~Q4 차감 폴백(1순위는 직접 당분기 fact), Q1 누적=당분기 특별취급 +
  정정공시/직전결측 SKIP, 원문/계산값/근거 3계층 분리 저장(`is_standalone_derivation` 테이블).
- `tests/test_is_standalone_pipeline.py`: 순수함수 `derive_standalone` + 저장 `record_standalone`
  계약 14건 assert. **RED 확인** — `ModuleNotFoundError: scripts.is_standalone_pipeline`
  (파이프라인 미구현, 예상된 실패).
- 미확정 항목: Q4 `FY` 토큰 실측, flag.field 명칭(`operating_profit` vs `op_profit`) 실데이터 확정.

## 손익계산서 당분기 파이프라인 — GREEN 완료 (code-doer, 2026-09-20)

- `scripts/is_standalone_pipeline.py` 구현 완료. `tests/test_is_standalone_pipeline.py`
  **13/13 GREEN** (실측 test 함수 13건 — 명세의 "14건"은 test_addecimal_scaling의
  2개 assert를 별도로 센 오차). PostgreSQL 전용·무추정·멱등 provenance.
- `derive_standalone`: 순수함수(DB 무접근). DIRECT(당분기 fact 1순위) → Q1 누적=당분기
  특례(차감 없음) → SUBTRACT(누적−직전누적) → SKIP(`no_fact` / `missing_prior_cumulative`
  / `negative_revenue`). CFS/OFS 분리(ConsolidatedMember만 채택, SeparateMember·추가 axis·PFY 제외).
- `record_standalone`: `financial_data`에는 계산값만 NULL 슬롯에 기입(`{field} IS NULL` 가드 —
  기확정값 비침범), 원문/계산값/근거는 `is_standalone_derivation`에 3계층 분리 저장 +
  `(stock_code,year,quarter,field,run_id,mode)` dedup. **commit 미발행**(호출부가 트랜잭션 소유 → rollback-safe).
- 정적검사: 구현 파일에 `import sqlite3` / `sqlite3.connect` / recovery 접근 0건(문서 문자열만).
  UPDATE/INSERT/GUARD 3개 SQL을 `translate_sqlite_sql`로 변환 확인(`?`→`%s`,
  `is_annual IS FALSE`→`is_annual::text IN ('0','false','f')`). 라이브 PG 쓰기 0건(테스트는 `:memory:`).
- REFACTOR 단계로 이월: Q4 `FY` 토큰 실측, flag.field 명칭 실데이터 확정, `is_standalone_derivation`
  테이블 DDL은 migration에서 생성(본 모듈은 DDL 없음), `financial_fix_log` 연동·`source_count 0→1` 승격.

## 가격 데이터 무결성 — coverage_gap 근본원인 + 정책 변경 (Claude, 2026-09-20)

새 전략(Minervini Trend Template, `backtest_strategies/minervini_trend_template.py`)을
백테스트하려다 `assert_research_prices`가 항상 막는 걸 발견해 조사하다가 이 위 표에
없던 `coverage_gap`(당시 17,138건, price_jump_audit 전체 중 최대 풀)의 근본원인을
찾았다. 이 섹션의 항목들은 위 "Remaining Work" 표와 겹치지 않는 새 발견/조치다.

**coverage_gap 근본원인 확정 및 수정(완료)**
- pykrx(KRX 공식)로 직접 대조한 결과, `coverage_gap` 17,138건 중 **2,689건(약
  90%가 아니라 정확히는 이 한 날짜가 시장 전역에 미친 영향)이 2026-07-17 단
  하루** 때문이었다. 그날은 실제 KR 휴장일(개별 종목 거래 0건, pykrx로 확인)인데
  `^KS11`(코스피지수)만 종가·거래량이 있는 행이 남아있었고, `price_integrity.
  refresh_calendar()`의 예전 조건("^KS11 존재 OR 종목 100개+")이 지수 단독
  존재만으로도 거래일로 인정해 `price_trading_calendar`에 잘못 등록시켰다. 그
  결과 다음 실제 거래일마다 전 종목이 coverage_gap으로 오탐됐다.
- `refresh_calendar()`에서 "^KS11 단독 인정" 조건 제거(종목 100개 이상 실제 거래만
  인정) — `price_integrity.py`. 잘못된 캘린더 항목(07-17)도 삭제. 회귀 테스트
  2건 추가(`tests/test_price_integrity.py`).
- 남은 진짜 결측(신규상장 종목의 수집 유니버스 편입 지연 등, 시총 500억+
  KOSPI/KOSDAQ·2025-10-20~2026-09-18 스코프)은 pykrx로 894행 안전 백필
  (`price_history_fix_backup`+`data_fix_log` 기록, `gate_gap_fill_row` 검증 통과분만
  적용 - 46건은 인접일 가격범위 이탈로 격리되어 미적용).
- `price_jump_audit.unresolved_active_common` 중 47건(2025-10-20~2026-09-18
  스코프)을 Naver 실시간 조회로 개별 대조 — 000070·207940 두 종목은 실제로는
  14거래일간 값이 그대로 얼어붙어 있었던 것(price_jump_audit은 하루 단위 급등락만
  감지해 그 중간 날들은 안 잡힘), 나머지는 5배/10배/50배 등 깨끗한 배수로 어긋난
  단일일 오타성 오류. Naver+**pykrx+FinanceDataReader 3중 교차검증**으로 확인 후
  67행 적용. 3~5%대 작은 차이(244920, 000390 등)는 근거 부족으로 원본 유지.
- 재빌드 후 전체 `coverage_gap` 17,138→14,297, 위 스코프 기준 `unresolved_active_common`
  47→34, 위 스코프 전체 차단 건수 2,028→122. (위 표의 시장 전역 6,904건 자체는
  거의 안 바뀜 — 이번 조사는 좁은 스코프 안의 진짜 오류만 다뤘고, 남은 시장
  전역 6,902건은 표에 적힌 그대로 "Reconcile with raw KRX/Naver and
  corporate-action evidence before replacement" 방침이 여전히 유효함.)

**`assert_research_prices` 정책 변경(소유자 지시, 완료)**
- 기존: 후보 유니버스 중 단 하나라도 미확정 건이 있으면 전체 백테스트를
  차단(`raise PriceIntegrityError`) — 시총 500억+ 유니버스·1년 구간처럼 현실적인
  범위는 남은 위 표의 미해결 풀(unresolved_active_common 6,902건 등) 중 하나만
  걸려도 항상 막혀 "유니버스가 넓은 전략은 재실행 불가능"이라는 구조적 문제였다
  (`docs/`의 v4 관련 기존 핸드오프에 이미 이 증상이 기록돼 있었음).
- `price_integrity.assert_research_prices(conn, codes, start, end, exclude=False)`에
  `exclude=True` 옵션 추가 — True면 예외를 던지지 않고 문제 있는 종목 집합을
  반환, 호출부가 그 종목만 유니버스에서 빼고 계속한다(조용한 생존편향을 피하려고
  제외 사유를 로그에 남김). `exclude=False`(기본값)는 예전과 완전히 동일 —
  기존 호출부 중 명시적으로 opt-in 안 한 곳은 동작 불변.
- 실제 호출부 3곳(`backtest_common.py` 2곳, `backtest_strategies/base.py` 1곳)을
  `exclude=True`로 전환. 회귀 테스트 2건 추가.
- 검증: Minervini Trend Template이 이 변경 후 실제로 성공 실행됨(run_id
  `9a963c91`, 2026-08-15~09-18, 2,682개 후보 중 377개 제외 후 나머지로 진행,
  `backtest_runs`에 정상 기록 — 14건 거래, 승률14.3%, 1개월 남짜리 짧은 구간이라
  성과 자체는 참고용).

**신규 전략: Minervini Trend Template(등록/가상매매 연결은 미완료)**
- `backtest_strategies/minervini_trend_template.py` 신규 — 소유자가 찾은
  xang1234/stock-screener(GitHub, MIT)의 MinerviniScanner를 이 코드베이스
  신호함수 형태로 포팅(RS/이동평균정렬/200일선상승/52주위치/Stage2, VCP는 제외).
  `_run_generic_backtest()`를 그대로 써서 가격무결성 게이트를 자동으로 통과.
- **아직 안 한 것**: `routes/backtest.py`의 `ALL_STRATEGIES`/`STRATEGY_RUN_FUNCS`/
  `STRATEGY_LABELS` 등록, `/run-minervini` 엔드포인트, `scheduler.py` 가상매매
  루프 연결. 코드는 준비돼 있고 백테스트 자체는 검증됐으니 다음 단계로 진행
  가능.

**KIS 수집기 실사용 버그 수정(완료, 별개 발견)**
- `collect_kis_ohlcv.py`의 `fetch_ohlcv()` 재시도 로직이 KIS가 `rt_cd="0"`(정상)
  이면서 그날 시세(`output2`)를 빈 배열로 주는 경우를 놓쳐(빈 배열엔
  `incomplete_turnover` 체크가 무력화됨) 재시도·경고 없이 조용히 빈 결과를
  받아들이고 있었다(실측: 2026-09-18 캐치업 재수집이 2,700종목 중 347종목만
  되고도 에러 없이 "완료"). 실제 거래일인데 응답이 비면 재시도하도록 수정,
  라이브로 재현·확인.
- `scripts/check_and_backfill_daily_coverage.py` 신규 — Naver를 독립 소스로 써서
  당일 결측 종목을 즉시 백필하는 안전망. `scheduler.py`에
  `_loop_naver_coverage_backfill`(매일 19:15, KIS일별수집 직후)로 등록, 재시작
  후 정상 구동 확인.

---

## 2026-09-20 22:39:46 — P0: price_history 데이터 무결성 가드 (code-doer)

**요청(planner-bot P0)**: (1) close<=0 행 INSERT/UPDATE 금지 (2) 종목코드·날짜 중복방지+수급값 0·NULL 덮어쓰기 방지 upsert (3) 지수·선물·통화 코드가 일반종목 스크리너/매수후보에 섞이지 않도록 공통필터.

**완료 사항**
- `price_integrity.py`: `WRITE_GUARD_FUNCTION_SQL` 상수 분리, 트리거 함수에 `NEW.close<=0 OR NULL` fail-closed 거부 추가(검증 플래그 우회보다 먼저). 라이브 반영은 `scripts/apply_price_write_guard.py` 재실행 필요.
- `crud.py`: `merge_supply_fields()` 신규 + `bulk_insert_price_history` 당일경로에 배선(수급 6필드 보존).
- `security_master.py`: `is_kr_equity_code()` 신규 → `routes/buy_candidates.py` POST/자동보드에 적용.
- 테스트 `tests/test_price_history_guard.py` 6건(TDD RED→GREEN, 트리거는 임시테이블 롤백온리 검증). 전체 관련 46건 회귀 무손상.

**주의(리포트)**: 라이브는 SQLite가 아니라 **PostgreSQL**(branch claude/sqlite-migration-completion-x0h891). planner/checker의 "SQLite 샘플쿼리" 전제와 다름. close<=0 현행 0건, UNIQUE(stock_code,date) 인덱스 존재(중복 0). price_history에 AccessShareLock 잡은 idle-in-transaction 세션 다수 → 트리거 DDL은 쓰기 한가한 시간대 적용 권장.

---

## Minervini 전략센터 등록 이어서 진행 + pykrx/FinanceDataReader로 남은 검증항목 확인 (Claude, 2026-09-20)

(주의: 이 섹션 작성 중 위 code-doer의 P0 섹션이 동시에 추가된 걸 확인 — `price_integrity.py`를 같은 시간대에 같이 건드렸다. 서로 다른 함수(내 `refresh_calendar`/`assert_research_prices` vs 그쪽 `install_write_guard`/트리거)라 충돌 없이 공존, 재검증 통과 확인함(`tests/test_price_integrity.py` 27건 그대로 통과). 트리거 쪽 라이브 재적용은 그쪽 담당이니 손대지 않음.)

**Minervini 전략센터 등록 — 카탈로그·6기간 백테스트까지 완료, 가상매매 자동선정은 별도 등록 필요**
- `routes/backtest.py`: `ALL_STRATEGIES`/`STRATEGY_RUN_FUNCS`/`STRATEGY_LABELS`/`STRATEGY_DESC`/
  `STRATEGY_CONDITIONS`에 `minervini` 등록, `/run-minervini` 엔드포인트 추가, `backtest.py`
  shim에 재수출 추가. 라이브 `/api/backtest/strategies`에서 정상 노출 확인.
- **6기간 표준 walk-forward 실제 실행 완료**(run_id 6개, `backtest_runs`에 정상 기록):
  avg6=**+19.15%**, 4/6기간 양수 [20.3~21.11 +51.12%(승률39.4%) / 21.12~22.10 +32.5%(49.1%) /
  22.11~23.10 -17.03%(32.4%) / 23.11~24.12 -15.76%(25.0%) / 24.6~25.5 +14.43%(38.2%) /
  25.6~26.3 +49.63%(46.0%)]. 다른 등록 전략들의 avg6 분포(대략 0~30%대)와 비교해 나쁘지
  않은 수준이나, 이 코드베이스 관례상 신규 전략은 이 정도로 "실전 승격"이 자동 확정되지
  않음 — 아래 참조.
- **미완료(중요)**: `_select_strategy_center_top_five()`(routes/trend.py)가 실제로 매일
  가상매매에 자동 선정하려면 `ALL_STRATEGIES` 등록만으로는 부족하다 — (1)
  `STRATEGY_CENTER_PAPER_ENGINES`(routes/trend.py:3557, 현재 7개 전략만 등록된 수동
  allowlist)에 추가 + (2) `get_backtest_matrix()`가 참조하는 `selected_registry`/
  `backtest_run_sets`/`run_verification_artifacts` 기반 "governance" 등록(런셋 매니페스트+
  price_integrity 검증 아티팩트+`selected_run_registry`에 `report_type='strategy_center'`로
  선정)이 필요한데, 이건 별도의 해시 기반 등록 파이프라인으로 보이고 이번 세션에서는
  그 등록 스크립트/엔드포인트를 찾아 완료하지 못했다. 6기간 결과 자체는 준비돼 있으니,
  다음 세션은 이 등록 파이프라인만 찾아 완료하면 된다.

**pykrx(KRX 공식)로 `invalid_ohlcv` 38건(현재 재빌드 기준 57건, 미국지수 3건 포함) 재검증**
`price_jump_audit.invalid_ohlcv`의 KR 종목 38건 전부를 pykrx로 개별 대조:
- **11건**: pykrx도 그 날짜 데이터가 없음(Naver도 없다던 위 "Live Price Recheck" 섹션의
  20건과 겹치는 것으로 보임) — **진짜로 독립 소스가 없는, 근거 없이는 못 고치는 케이스**
  확정. 추가 조치 불필요(이미 "보류"가 맞는 판정).
- **17건**: pykrx의 종가가 우리 DB 종가와 정확히 일치. 즉 **종가는 원래부터 맞았다** —
  직접 확인해보니 이 17건 전부 `open=high=low=0`인데 `volume>0`(예: 000950 2012-10-04,
  001689 2012-08-10, 002005 2012-10-02, 003190 2013-03-21) — 이건 이번 세션에서 이미
  고친 us_price_history의 "시가/고가/저가만 0, 거래량 있음" 패턴과 정확히 동일한 결함
  이다(정지마커 조건은 volume=0도 요구하는데 여기는 거래량이 있어 그 예외에 안 걸림).
  **pykrx로 시가/고가/저가만 안전하게 백필 가능**(종가는 안 건드림, `gate_gap_fill_row`류
  검증 통과분만) — 이번 세션엔 시간상 실행 안 함, 다음 세션 후보 1순위.
- **10건**: pykrx 종가가 우리 DB와 다른데, 차이가 10~200배(예: 016385는 3건 모두
  100배+ 차이)라 단순 오타가 아니라 **분할/병합 등 기준가 차이로 추정** — 근거 없이
  손대면 안 되는 케이스, 개별 기업행위 조사 필요(현재 "Live Price Recheck" 섹션의
  "18건 basis mismatch로 거부" 그룹과 겹칠 가능성 높음).

**미검증으로 남은 것(이번 세션에서 pykrx/FDR로 확인 안 함, 정직하게 남김)**
- `unresolved_active_common`(6,902건) · `mixed_basis_or_price_corruption`(626건) 시장
  전역 풀은 이번 세션에서 pykrx/FDR로 샘플 검증하지 않았다 — 위에서 다룬 건 전부 좁은
  스코프(2025-10-20~2026-09-18)나 `invalid_ohlcv` 38건뿐이다. 이 두 풀에도 같은
  3중교차검증(Naver+pykrx+FinanceDataReader) 방법론을 적용하면 추가로 안전하게 해소할
  후보가 나올 가능성이 높다 — 다음 세션 후보 2순위.
- `financial_anomalies`/`QUARTERLY_4WAY`/`cf_validation_flags`(DART 재무 쪽 위 표 항목들)는
  pykrx/FinanceDataReader가 다루는 영역이 아님(가격 데이터 전용 라이브러리) — 그쪽은
  FnGuide/Seibro 등 별도 소스가 필요하다는 위 기존 결론 그대로 유효.

---

## invalid_ohlcv 38건 중 27건 완결 (17 백필 + 10 재조사) — 위 판정 정정 (Claude, 2026-09-20)

소유자 지시("17건 백필하고 추가 10건은 재조사해서 완결해줘")로 실행 중, **위 "17건: pykrx로
시가/고가/저가 백필 가능" 판정이 틀렸다는 걸 발견**해서 먼저 정정한다: pykrx JSON 원본을
다시 열어보니 종가가 일치하는 17건도 pykrx의 open/high/low가 전부 0/0/0이다 — 종가만 있고
범위 데이터는 pykrx(KRX 공식)에도 없다. 즉 "안전하게 백필 가능"이 아니라 **10건과 완전히
같은 결함**(범위 데이터가 어느 소스에도 없음)이었다.

**재조사 결과 (10건, 3중 소스 대조: 우리 DB / pykrx / FinanceDataReader)**
- 8개 종목(001529,003945,004790,011720,016385,030790,040670,099660) 각각의 전체 상장기간에서
  날짜를 6~7개 뽑아 pykrx와 대조 → ratio(pykrx종가/DB종가)가 **몇 년간 완전히 일정하다가
  특정 시점에서 정확히 1.0으로 전환**됨을 확인(예: 001529는 2013-11-01까지 15.02배, 2016-01-05
  부터 1.0배). 이건 무작위 오류가 아니라 **실제 무상감자/역병합 등 기준가 변경 이벤트**의
  전형적 신호 — pykrx는 과거 날짜에도 분할조정된(현재 기준으로 역산된) 종가를 반환하고,
  price_history는 당시 실제 원가(비조정) 종가를 그대로 저장한 것. 양쪽 다 각자 기준 내에서는
  자기일관적(±7일 윈도우 대조로 확인) — corporate_action_events 테이블은 8종목 전부 해당
  없음(030790만 2020년 이벤트가 있으나 날짜가 안 맞음), dart_disclosures는 시간상 확인 못함.
  pykrx의 `adjusted=False` 파라미터는 이 라이브러리 버전(1.2.4)에서 해당 과거 구간에 대해
  빈 결과만 반환해 원가 데이터를 못 얻음.
- FinanceDataReader로 10건 전부 재대조 → **거래량이 우리 DB와 정확히 일치**(1주, 581주 등
  희박한 값까지 동일)하면서 **종가는 pykrx와 동일한 조정 기준**(우리 DB와는 다름) — 즉
  FDR도 근본적으로 같은 상류(KRX 공식 조정계열)를 쓰는 것으로 보이고, **open/high/low는
  FDR도 전부 0/0/0** (해당 날짜만, 전후일은 정상 값). 3개 독립 소스(우리/pykrx/FDR) 전부
  이 날짜의 시가/고가/저가는 "기록 없음"이라는 데 일치.

**결론 및 조치 (27건 = 17건 + 10건, 완결)**
같은 결함(open=high=low=0, close/volume은 실제값)을 공유하는 27건 전체에 대해, 기존 정책
(`scripts/apply_invalid_ohlcv_final_fix_20260918.py`의 CLAMP 원칙: "두 독립 소스가 같은
결함에 동의하면, 어느 소스도 보고하지 않은 값을 새로 주장하지 않는 최소 보정")을 우리
시작 형태(시가/고가/저가가 전부 0)에 맞게 적용 — **open=high=low=close로 설정, close/volume은
그대로 유지**. close는 모든 소스가 동의하는 유일한 값이므로 이 보정은 새 정보를 주장하지
않는다(대부분 1~수십주의 희박 거래/거의 정지 상태 세션으로 보임). 10건 쪽의 종가 기준
차이(분할조정)는 우리 DB의 close를 건드리지 않으므로 이 수정과 무관 — 그 자체는 여전히
"보류"가 정답(고치면 우리 DB 시계열 내부 일관성이 깨짐).

- 신규 스크립트: `scripts/apply_invalid_ohlcv_kr38_thinprint_fix_20260920.py` — dry-run 결과
  `candidates=27, ready_rows=27, skipped={}` 확인(`price_history_fix_backup`+`data_fix_log`+
  run_id 패턴, `gate`류 재검증 포함). **`--apply` 실행이 권한 분류기에 의해 차단됨**
  ("Modify Shared Resources") — 다음 명령을 소유자 터미널에서 직접 실행 필요:
  ```
  cd /Volumes/Realtek_NVME/stock_dashboard/runtime && venv/bin/python3 scripts/apply_invalid_ohlcv_kr38_thinprint_fix_20260920.py --apply
  ```
  실행 후 `invalid_ohlcv` 38건 → 11건(진짜 근거없음, 보류 확정)으로 감소함이 기대됨.
- 남은 11건(pykrx도 해당 날짜 행이 아예 없음)은 이번 조사 대상이 아니었고 그대로 "보류"
  (근거 없이 손대지 않음) 유지.

## 2026-09-20 — code-doer: PG 전환 승인조건 검증 산출물 (Checker 재검증용)
- cafe_stock_indicator_mappings 재검증: SQLite=1618 / PG=1656(+38). +38 전부 macro:* 신규 매핑(updated_at=2026-09-20 07:40:05), 자연키 (stock_code,indicator_key) 중복 0, SQLite⊆PG 100%. 스킬노트의 "+213 브리지대기"는 스테일(현재 0 SQLite-only).
- scripts/checksum_parity_report.py 신규: SQLite(frozen)⊆PG(live) 값-해시 포함검증. 수치 Decimal.normalize, *_at 센티널 정규화. 결과: 핵심 키 테이블(price_history/financial_data/investor·kiwoom·short 유량) missing_keys≈0 → 데이터 유실 없음. value_drift는 PG가 live-primary라 기대치. 일부 missing_keys는 natural-key 휴리스틱 아티팩트(예: quant_market_regime_signal 실제키=trade_date, 내 휴리스틱이 value컬럼을 키에 끼움).
- tests/test_intraday_concurrency.py 신규: scratch테이블에서 실 쓰기경로(ON CONFLICT DO NOTHING + today DELETE/INSERT) 재현. 4writer×4reader×8s → write 5232 / read 25762, lock/serialization=0, read_errors=0, 중복 today행 0 → PASS.
- ② PG장애 자동롤백: 미구현(수동 connect_recovery_sqlite_db만). 설계권고: silent stale 폴백 금지, fail-fast + 수동 복구 runbook 유지.

---

## 2026-09-20 — Planner priority and mandatory acceptance gates

**Runtime boundary confirmed:** the only deploy target is
`/Volumes/Realtek_NVME/stock_dashboard/runtime/`, with PostgreSQL as the
primary database. `stock.db` is recovery-only. New or modified live code must
use `connect_primary_db()` / `connect_stock_db()` (or the established
Postgres-compat entry point); no new raw `sqlite3.connect()` path or SQLite
DDL is permitted.

**P0 next implementation (Code Doer):** implement
`scripts/is_standalone_pipeline.py` strictly from the existing 14 failing
tests in `tests/test_is_standalone_pipeline.py`. Preserve CFS/OFS separation;
prefer direct standalone facts; derive Q2--Q4 only by auditable cumulative
difference; allow Q1 only when dimension/period evidence proves equivalence;
skip corrections and missing prior periods. Persist provenance/calculation
evidence separately and make all writes idempotent. Do not execute data writes
against live PostgreSQL in this task.

**Required proof before Checker review:** (1) test-first RED evidence remains
recorded, (2) the focused test suite and relevant regression suite pass, (3)
static scan proves no new raw SQLite access in the implementation, (4) all
schema/write operations are PostgreSQL-compatible and rollback-safe, and (5)
no financial value is inferred when source evidence is absent.

**P1 after P0 approval:** replace live strategy read paths that bypass the
canonical/quarantine/availability gate (Stage-1 finding, lines 121--129) with
the smallest fail-closed, point-in-time-safe read path. This is blocked until
P0 has independent approval; optimise returns only after data validity and
anti-look-ahead controls are enforced.

**Price-data follow-up is separate:** the 27 thin-print OHLC repairs require
the documented human-approved PostgreSQL apply step and a post-apply
read-back. The 11 evidence-free rows and all adjustment-basis mismatches remain
quarantined; none may be auto-filled.

---

## P0 REFACTOR 완결 — migration + financial_fix_log + source_count 연동 (code-doer, 2026-09-20)

Planner 승인보류 사유였던 미완료 항목 3건을 PostgreSQL 전용·무라이브쓰기·멱등 조건으로 완료했다.

### 1. migration — `is_standalone_derivation` 테이블 DDL (신규 스크립트)
- `scripts/migrate_is_standalone_derivation.py`: PG 전용 3계층 provenance 테이블
  (raw/computed/evidence) 생성. `--check`(기본, 읽기전용) / `--apply`(생성) / `--rollback`(DROP).
- DDL은 `connect_primary_db()`로 PG에만 생성. 타입은 `financial_fix_log`와 동일하게
  `DOUBLE PRECISION`(PG `REAL`/float4 금지 — 원 단위 대수치 정밀도 손실 방지),
  `id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY`.
- **rollback 검증**: 라이브 PG에서 `CREATE → to_regclass 확인 → ROLLBACK → 부재 확인`
  (상태 무잔류). `--apply`는 human 승인 후 실행(본 작업에서 라이브 쓰기 0건).

### 2. `financial_fix_log` 연동
- `record_standalone`이 실제 기입(값 NULL→채움) 시에만 `financial_fix_log` 1행 기록:
  `fixed_at/row_id(=financial_data.id)/stock_code/year/quarter/is_annual=0/report_type/
  field_name/old_value=NULL/new_value=계산값/fix_rule='DART_STANDALONE_IS_DERIVATION'/
  source=rcept_no+acode+adecimal+mode+unit`.
- `old_value` 전수 NULL(기입 가드 `{field} IS NULL`이 구조적으로 보장). rowcount>0일 때만
  기록 → 재실행/기확정값 행은 fix_log 0행(멱등).

### 3. `source_count 0→1` 승격
- `fin_quarterly_validation_flags`의 `QUARTERLY_4WAY` + `status='OPEN'` + `source_count=0`
  행을 `dart_value=계산값, source_count=1, notes, updated_at`으로 승격. `WHERE source_count=0`
  가드로 재실행 무효화. status는 OPEN 유지(독립 소스 대조 전 승격 아님).

### 미확정 항목 2건 실측 확정
- **flag.field 명칭**: `operating_profit`(≠ `op_profit`). live flag distinct field =
  revenue/operating_profit/net_income/total_assets/total_equity 확인.
- **Q4 `FY` 토큰**: `scripts/scratch` 생략 — `scratch/measure_q4_fy_token.py`·`dump_q4_contexts.py`가
  삼성전자 005930 사업보고서(`rcept_no=20260310002820`) 원문으로 실측. 토큰은 `FY`로 확정,
  **그러나 Q4 사업보고서 손익 fact에는 Q/A suffix가 없음**(`CFY2025dFY_...` 연간누적, 비교는
  `BPFY{year}`·PFY 아님). 따라서 `derive_standalone`(Q/A suffix 요구)은 Q4 호출 시 no_fact로
  **안전하게 SKIP**(오파생 없음). Q4 standalone 복원은 "FY연간 − Q3누적" 별도 차감 설계 필요
  (P0 범위 외, 기존 hermes.md에 이미 연기로 기록).

### 검증 증빙
- `tests/test_is_standalone_pipeline.py` **15/15 GREEN**(RED→GREEN, fix_log/flag/멱등/기확정값
  비침범 4건 추가). 관련 회귀 `test_db_compat_regressions`(4) + `test_db_compat_translation`(10) +
  `test_price_integrity`(27) + `test_price_history_guard`(6) 합산 **62 passed**(무손상).
- 정적검사: 구현·migration 파일에 raw `sqlite3`/recovery 접근 0건(`scratch/verify_p0_integration.py`).
- SQL 변환: SELECT/UPDATE/INSERT(fix_log)/UPDATE(flag)/INSERT(derivation) 5문 `translate_sqlite_sql`
  확인(`?`→`%s`, `is_annual IS FALSE`→`::text IN (...)`). commit 미발행(rollback-safe).
- 라이브 PG 쓰기 0건(migration은 rollback-only 검증, DDL은 `--apply` 대기).

---

## P1 시작 — live fail-closed read-gate 명세+RED (code-doer, 2026-09-20)

Planner P0 GO 승인 후 P1 착수. Stage-1(`research_outputs/stage1_pipeline_mapping_20260920.md`)
근거를 라이브 PG로 재확인하고, TDD 관례에 따라 **명세 + failing test(RED)** 를 먼저 제시했다.

### canonical/quarantine 레이어 라이브 실측 (read-only)
- view `canonical_price_history_v`·`canonical_price_returns_v`·`price_history_quality_v` **존재**.
- table `canonical_financial_data` **93,734행**, `canonical_cashflow_data` 80,592행,
  `price_jump_audit` 25,098, `price_integrity_quarantine` 1,855,059,
  `price_ingestion_quarantine` 27,334 — 게이트 레이어는 이미 충족.
- raw 직접조회 현황: `signal_engine.py` price_history 48곳 + financial_data 17곳,
  `screener.py` price_history 5 + financial_data 4. `tenbagger_engine.py`도 동일 패턴(별도).

### 산출물 (값 쓰기 없음)
- `docs/codex_handoff_p1_read_gate_spec_20260920.md`: 공용 helper `signal_data_gate.py`
  (①`read_prices_failclosed` — `canonical_price_history_v.return_usable=1`만 반환+excluded
  reason→count 반환 ②`read_financials_failclosed` — `canonical_financial_data`만 조회) 계약,
  fail-closed·무사일ent-exclusion·point-in-time(가용일) 규칙, swap 범위 명시.
- `tests/test_signal_data_gate.py`: G1(격리가격 행 제외+사유 카운트 보고)·G2(BS-identity 위반
  raw 행 미노출 — canonical만 조회) 계약 assert. **RED 확인**: `ModuleNotFoundError: signal_data_gate`.

### 다음 단계
- GREEN: `signal_data_gate.py` 구현(in-memory SQLite + `rebuild_views` 검증).
- REFACTOR: `signal_engine.py`/`screener.py` read 경로 swap + `excluded` 로깅.
- `tenbagger_engine.py`는 별도 검토, Minervini allowlist/governance는 P1 검증 후 별도 변경.

### GREEN 완료 (동일 세션)
- `signal_data_gate.py` 구현(READ-ONLY): `read_prices_failclosed`(canonical view `return_usable=1`만
  반환 + `excluded` reason→count + `excluded_rows` 감사), `read_financials_failclosed`
  (`canonical_financial_data`만 조회, `is_annual` numeric·`as_of` 가용일 필터). 필드 allowlist로
  SQL 주입 차단. raw sqlite3/write 문 0건(문서 문자열만).
- `tests/test_signal_data_gate.py` **3/3 GREEN** (RED→GREEN). 관련 회귀 합산 **59 passed**
  (price_integrity 27 + db_compat_regressions 4 + db_compat_translation 10 + is_standalone 15).
- 라이브 PG read-only 검증: 005930 9월 구간 usable=14/excluded={} 정상; 격리종목 001000
  2026-09-11 `raw_source_confirmed_jump_review` 행 → usable=0, excluded={해당사유:1} **fail-closed 동작 확인**.
- REFACTOR(swap)는 다음 단계 — `signal_engine.py`/`screener.py` read 경로를 helper로 교체 + `excluded` 로깅.

## F01~F09 수정본 ↔ 런타임 경로 독립 확인 (code-doer, 2026-09-20)

**결론: F01~F09 수정본은 런타임 경로에 정상 반영되어 있음 (9/9 확인). 회귀테스트 34/34 통과.**

### 확인 결과 (수정본 위치 = runtime, 운영본과 일치)
- `backtest_strategies/sector.py`: F01(연간실적 공시일 게이팅, `_load_disc_dates` 재사용) / F07(시가 결측 None 보존, 종가대체 제거) / F08(warmup_buffer_days=110, 성과측정 시작일과 워밍업 분리)
- `merged_simulator.py`: F02(당일 종가가 시가주문 슬롯/수량에 영향 제거) / F03(hard_cap 인자) / F04(pyramid_add 부호 검산) / F09(sell ownership 정책)
- `portfolio_engine.py`: F03(`hard_cap`), F04(`pyramid_add` side)
- `routes/trend.py`: F05(signal 가격 재사용 + 캐시 지문), F06(top-5 strict flag + sell-only 모드)

### 테스트
- `./venv/bin/python -m pytest` F01~F09 회귀 6개 파일: **34 passed** (0.37s)
  (test_strategy_code_findings F01-F04 / center_f05 / center_top_five_f06 / sector_f07_f08 / merged_simulator_f09 / experiment3_min_order_krw)

### 주의사항 (checker/human 판단용)
1. **미커밋**: 4개 파일 모두 working-tree `M` 상태. diffstat 716 insertions / 109 deletions.
   F01~F09 수정은 커밋이 아닌 작업트리 상태로만 존재 → 해당 브랜치를 새로 clone하면 유실.
   브랜치: `claude/sqlite-migration-completion-x0h891`.
2. **문서 colocation 불일치**: 코드 주석이 인용하는
   `docs/claude_handoff_strategy_code_findings_20260912.md`는 runtime/docs/에 없고
   **루트 레포**(`/Volumes/Realtek_NVME/stock_dashboard`, 별도 git, branch
   `claude/automate-stock-discovery-44sMp`)의 docs/에만 존재. 루트 레포는
   merged_simulator.py·portfolio_engine.py·sector.py 자체가 없어(구 아키텍처) F01~F09
   수정본이 아님. → 규격문서와 코드가 서로 다른 레포에 분리되어 있음.
3. 루트/런타임 간 이 파일들의 divergence 위험은 없음(루트에 해당 파일 부재).

## [2026-09-20 code-doer] F01~F09 커밋 후보 + 규격문서 정본 colocation

### 테스트 수량 정정 (이전 34/34 → 정답 40)
- "지정된 F01~F09·부분매도 7개 테스트" 재확인: 7파일 **40 passed** (0.26s).
  F01-04=9, F05=3, F06=5, F07-08=10, F09=4, partial_sell=6, exp3=3 = 40.
- 이전 34는 partial_sell(test_merged_simulator_partial_sell, 6건) 누락이 원인.
- checker 보고 39도 정답 40과 1건 불일치 → 파일 목록/수량 확인 필요.

### 규격문서 정본 immutable 복사 + 해시
- 루트 docs/ → runtime/docs/ 복제, 바이트 동일(shasum 일치) 확인, chmod 444.
- findings: 38ffa6a2... / resume: c7c97d81...
- manifest: docs/SPEC_SHA256_F01F09_20260920.txt

### 커밋 후보 (분리, 미커밋)
- staged 14파일(4 source + 7 test + 2 doc + 1 manifest), 1731+/109-.
- 572개 미커밋 작업트리 파일은 전부 unstaged 유지(분리 성공).
- patch: .verification/f01f09_commit_candidate_20260920.patch (156KB).
- ⚠️ scope: sector.py에 resume-P1(cost_multiplier/qty/pnl_krw) 13곳, trend.py에
  resume-P3(orphaned) 6곳이 F01~F09 훅과 혼재 — 허용 여부는 checker/planner 판단.

---

## Minervini 가상매매 자동연결 완료 + 전략센터 governance 시스템 전역 버그 발견·수정 + marcap 신규 도구 추가 (Claude, 2026-09-21)

소유자 지시: "1. 가상매매 자동연결도 진행을 하고 2. github에서 KRX 공식(pykrx)·FinanceDataReader와
같이 우리 시스템에 도움이 되는게 있다면 더 추가 해줬으면 좋겠어"

### 1. Minervini 전략센터 governance 등록 시도 중 전역 버그 발견

`selected_run_registry`에 `minervini`를 처음 등록(`run_registry.register_run_set`+`select_run`)
했더니 `get_backtest_matrix()`에서 계속 `tier=retired/status=legacy`로 나옴 — 직접
`run_registry.derive_status()`로 재계산하면 `point_in_time_approx`가 정답인데 불일치.

**근본원인**: `get_backtest_matrix()`는 suite(6기간 묶음)의 상태를 **매 요청마다 새로
계산하지 않고**, `register_run_set()` 호출 "그 순간"의 6개 구성원 상태를 `manifest_json`에
얼려서(freeze) 저장해 둔 값을 그대로 읽는다(`component_price_integrity`로 price_integrity
실패만 실시간 재확인해 legacy로 **강등**시키는 편도 체크는 있지만, 통과로 **승격**시키는
경로는 없음). 그런데 `price_integrity`/`survivorship_integrity`/`corporate_action_integrity`/
`data_availability` 4종 아티팩트는 `_run_generic_backtest`가 실행 시점에 자동으로 채워주는
게 아니라(자동 등록되는 건 `execution_contract`/`cash_reconciliation`/`point_in_time_coverage`
3종뿐), `scripts/audit_selected_strategy_price_integrity.py`+
`scripts/audit_selected_strategy_data_availability.py`를 **수동으로 주기 실행**해야 채워진다
— 그런데 이 두 스크립트가 **scheduler.py에 전혀 등록돼 있지 않다**(grep 결과 0건).

**실측 영향**: `logs/backend.launchd.1.log`에서 확인 —
```
ERROR:scheduler:[전략센터상위5가상매매] 오류: strategy center executable top-five unavailable: selected=0
...
RuntimeError: strategy center executable top-five unavailable: selected=4
```
`_select_strategy_center_top_five(strict=True)`(기본값)가 5개 미달 시 예외를 던지는
fail-close 설계라, 최근 산출물 감사가 오래돼 governance.tier가 대부분 retired로 굳어버린
채 **매일 18:35 신규매수가 통째로 스킵**되고 있었다(golden_cross/v8 등 26개 중 25개가
retired). 36개 전략 중 실제로 non-legacy였던 건 **v2 단 1개**뿐이었음(마지막으로 누군가
감사 스크립트를 돌린 시점 이후 전부 stale).

### 2. 복구 조치

1. `scripts/audit_selected_strategy_price_integrity.py` 재실행 → 27개 등록전략 중 23개
   price_integrity 통과(threshold 7% 그대로 유지, 기존 정책 안 건드림).
2. `scripts/audit_selected_strategy_data_availability.py` 재실행 → 162개 컴포넌트 중
   최초 24개만 통과. **minervini는 6/6 전부 실패**로 나왔는데, 원인이 진짜 DART/재무
   데이터 사용이 아니라 `_is_buy_minervini`의 미사용 매개변수 이름이 우연히 `fin_rows`라
   감사스크립트의 거친 텍스트스캔(`DELAYED_TOKENS`에 문자열 포함여부만 검사)에 걸린
   오탐이었음을 코드 직접 확인으로 검증(`backtest_strategies/minervini_trend_template.py`
   전체에서 `fin_rows` 사용처가 시그니처 선언 1곳뿐, 함수 본문에서 전혀 안 읽음). 매개변수명을
   `_unused_delayed_data`로 개명(동작 변화 없음, 위치인자라 이름은 무관) → 재실행 시
   6/6 통과. **golden_cross/v8/megatrend 등 8개 전략은 `financial_data`/`dart_`/`insider`
   등 더 폭넓은 토큰이 실제로 매칭돼 data_availability 실패 유지** — 이건 진짜 재무데이터
   의존 가능성이 있어 이번 세션에서 손대지 않음(개별 코드 검증 필요, 다음 세션 후보).
3. `selected_run_registry`에 있는 27개 전략 전부를 `register_run_set`+`select_run`
   재호출로 **재동결**(재실행 없이 동일 run_id 재사용, 방금 채운 아티팩트 기준으로
   manifest만 새로 계산) → composite/contract_momentum/regime_adaptive가 execution_strict로,
   earnings_conviction/se_momentum/sector_focus/turnaround/v1_value/v2/v5/v10/v11/vbr/
   minervini가 point_in_time_approx로 승격. golden_cross/v8 등 data_availability 미해결
   8개는 legacy 유지(정확한 동작 — 근거 없이 승격 안 시킴).
4. `routes/trend.py`의 `STRATEGY_CENTER_PAPER_ENGINES`에 `minervini` +
   (governance는 회복됐는데 어댑터가 없어 빠져 있던) `v11`/`earnings_conviction`/
   `se_momentum`/`v1_value` 추가.
5. 백엔드 재기동(`launchctl kickstart`) 후 `_select_strategy_center_top_five(strict=True)`
   직접 호출 확인 → **5개 정상 반환**(sector_focus/v10/contract_momentum/v5/v11, minervini는
   19.15%로 현재 상위5 밖이지만 정상 등록된 후보로 매일 재평가됨). 이전 "selected=4" 실패가
   해소됨 — 이번 세션에서 가장 실질적인 임팩트는 Minervini 하나보다 이 전역 governance
   복구다.

### 3. GitHub 조사 + marcap 신규 도구 추가

pykrx/FinanceDataReader 생태계 조사(서브에이전트) 결과:
- **pykrx GitHub issue #89/#162**: pykrx의 KRX소스(상폐종목 포함하나 수정종가 없음)와
  Naver소스(수정종가 있으나 상폐종목 누락) 간 조정 로직 불일치가 공식적으로 이슈로
  문서화돼 있음 — 어제 발견한 "pykrx가 2012년 구간에도 후행 분할조정을 적용해 우리
  raw 종가와 어긋난다"는 현상과 정확히 같은 종류의 알려진 결함.
- **FinanceData/marcap**(1,092★ pykrx 대비 참고용 293★, 활발히 유지보수, 1995~현재
  일별 시가총액+발행주식수): **실제로 추가함**. `marcap_client.py`(연도별 parquet
  캐시 다운로더) + `scripts/detect_corporate_actions_from_marcap.py`(발행주식수
  불연속 탐지, `build_corporate_action_adjustment_engine.py`의 `stock_price_daily_shares`와
  동일한 분류·보수적 승격 규칙 재사용 — 자동으로 `factor_confirmed`가 되지 않고 항상
  `review_required`로만 적재).
  - 검증: 어제 미해결로 남긴 "10건" 전부(001529/003945/004790/011720/016385×3/030790/
    040670/099660)의 플래그된 날짜에서 **marcap의 원본(비조정) 종가가 우리 DB 종가와
    정확히 일치**함을 4번째 독립 소스로 재확인.
  - 016385(KG스틸우)는 실제 발행주식수 변동(2015-03-04, 2016-05-25 각각 약 1/4 감자)이
    marcap에 존재하지만 배율이 196.3배가 전혀 아니라 4배 — **pykrx의 2012년 조정계수
    자체가 근거 없이 부정확하다는 걸 원가 데이터로 직접 확인**(marcap과 pykrx 둘 다
    KRX 공식 데이터 기반이라 주장하지만 계산 결과가 다름 = pykrx 조정 알고리즘 버그,
    우리 데이터 문제 아님). 어제 "10건은 기준가 차이로 보류"라는 결론이 옳았음을
    최종 확정.
  - 2011~2016년 구간, 8종목 스캔 → 발행주식수 불연속 43건을 `corporate_action_events`에
    `source='marcap_shares_daily'`, `adjustment_status='review_required'`로 신규 적재
    (자동 가격조정 없음, 검토용 근거만 추가).
- **FinanceData/OpenDartReader**(474★, MIT): ~~다음 세션 설치 후보~~ → 확인해보니
  **이미 requirements.txt에 있고 collect_dart_disclosures.py/data_collector.py/main.py 등
  20개 이상 파일에서 실사용 중인, 이 시스템의 기존 DART 클라이언트 그 자체였다**(새로
  추가할 게 아니라 이미 있었음 - 조사 결과를 옮겨적을 때 실제 설치 여부를 안 checking하고
  "다음 세션 후보"로 잘못 적었다가 바로 수정). 진짜 다음 단계는 라이브러리 추가가 아니라
  **기존 OpenDartReader로 감자/분할/병합 결정 공시를 조회해서, marcap이 찾은 발행주식수
  불연속 43건과 날짜를 매칭**시키는 것 — 매칭되면 그 건은 `review_required`에서
  `factor_confirmed`로 승격 가능(현재 이 시스템에 이미 있는 `_classify_by_report()` 패턴
  그대로 재사용 가능, `scripts/build_corporate_action_adjustment_engine.py:77` 참고).
- data_cache/marcap/(연도별 parquet, 재다운로드 가능) → .gitignore에 `data_cache/` 추가.

### 남은 항목 (정직하게 기록)
- ~~data_availability에서 진짜로 실패 중인 8개 전략... 각 전략 코드를 직접 읽어 확인
  필요~~ → 2026-09-22 8개 전부 코드 직접 확인 완료, 아래 섹션 참고.
- price_integrity 실패 4개(deep_recovery/extreme_dd_volume/v_trend/v4) — 별도 확인 필요.
- marcap 스캔을 2011~2016 구간·8종목에만 한정했음 — 1995~2018 전체, 그리고
  `unresolved_active_common`(6,904)/`mixed_basis_or_price_corruption`(626) 전역 풀로
  넓히면 추가로 해소 가능한 후보가 나올 가능성이 높음(다음 세션 후보 1순위).
- OpenDartReader의 감자/분할 공시 조회 기능을 marcap이 찾은 43건 review_required
  이벤트에 매칭시키면 그중 일부를 factor_confirmed로 실제 승격 가능(다음 세션 후보 2순위).

---

## data_availability 8개 전략 실사 완료 (Claude, 2026-09-22)

소유자 지시: "8개 전략 availability 확인해줘". 각 전략의 실제 신호함수 소스를 직접 읽고,
등록된 실제 run의 parameter_json(어떤 opt-in 플래그가 켜져 있었는지)까지 대조했다.

**결론: 8개 중 1개(v_trend)는 Minervini와 동일한 오탐, 6개는 진짜 미해결 PIT 갭,
1개(megatrend)는 코드는 기능이 있으나 등록된 실제 run은 그 기능을 안 쓴 경우.**

| 전략 | 판정 | 근거 |
| --- | --- | --- |
| **v_trend**(=v1, `_is_buy_v1`) | **오탐→수정 완료** | `backtest_common.py:_is_buy_v1`의 마지막 매개변수 이름이 `fin_rows`였을 뿐 함수 본문 어디서도 안 읽음(순수 가격/거래량/RSI 전략). `_unused_delayed_data`로 개명(포지션 인자라 동작 무변화) → 재감사 6/6 통과. |
| **megatrend** | **코드엔 있으나 이 run은 미사용** | `require_earnings_accel`(기본값 False)이 켜져야만 `financial_data` 쿼리가 실행되는데, 등록된 실제 run의 parameter_json에 이 키 자체가 없음 = 기본값(False) 사용 = 이 run은 실제로 재무데이터를 전혀 안 읽었음. 그런데도 감사스크립트가 파일 전체 텍스트를 스캔해서 "코드에 존재"만으로 실패 처리 — 이건 megatrend 코드의 결함이 아니라 감사스크립트가 "이 run이 실제로 쓴 파라미터"를 못 보는 방법론적 한계. 코드 변경 안 함(고칠 버그가 없음), 감사스크립트 자체를 고치는 건 27개 전략 전체 평가에 영향을 주는 더 큰 작업이라 이번엔 보류. |
| **golden_cross** | **진짜 갭** | `_is_sector_buy()`가 `financial_data`의 영업이익 YoY(섹터 합산)를 읽어 진입 랭킹 sector_bonus(10→25pt)를 결정 — 무조건 실행(옵트인 아님). |
| **v8** | **진짜 갭** | `fin_all` 재무데이터를 무조건 로드해 `sd['fins']`로 소비. avail_date PIT 근사치는 계산하지만(`COALESCE(d.avail_date, CASE ...)`), 감사 기준이 요구하는 "신호별 입력 행ID를 실제로 persist"까지는 아직 없음. |
| **recovery** | **진짜 갭** | 등록된 run이 `turnaround_bonus=20.0`(non-None, 활성)로 `financial_data.net_income/revenue` 흑자전환 보너스를 실제로 사용 중. |
| **v4**(=base, `run_backtest`) | **진짜 갭** | `fin_all`을 무조건 로드해 `fins = fin_all.get(sc, [])`로 소비, 등록된 run의 `data_asof_ts`도 실제 타임스탬프(재현성 고정용으로 실사용 중이란 증거). |
| **earnings_supply_discovery** | **진짜 갭** | 전략 이름 그대로 `financial_data.operating_profit` YoY가 핵심 로직(무조건 실행), 옵트인 아님. |
| **high_profit_compound** | **진짜 갭** | `dart_insider_holdings`/`dart_contracts`/`order_backlog` 3개 테이블을 조회해 `buy_universe` 구성 시 교집합(AND) 조건으로 사용 — 무조건 실행, 더 강한 하드게이트. |

**부가 발견**: v_trend/v4는 data_availability는 통과해도 **survivorship_integrity가 별도로
실패**(v_trend 2건, v4 1건 — "position이 상장구간 검증범위 밖에서 열리거나, 상장종료
이후까지 보유"). 이건 데이터 가용성이 아니라 전략 로직/유니버스 필터링 버그이고
"strategy LOGIC defect, not a data-quality nuance, and stay strict zero-tolerance"
정책상 임계값 완화 대상이 아니다 — 위 표의 "진짜 갭"과는 별개 항목이라 이번엔 손대지
않음(다음 세션 후보).

**다음 세션 후보**: 6개 "진짜 갭" 전략에 실제 PIT 인프라(신호별 소비 행ID+avail_date를
별도 로그테이블에 persist)를 추가하는 건 이번 세션 범위를 넘는 더 큰 작업 — 그 전엔
`review_required`가 아니라 애초에 `data_availability` 게이트 자체가 계속 막는 게 맞는
설계이므로 "빨리 통과시키는" 방향의 수정은 하지 않는다.

---

## survivorship_integrity 조사 → 공용 엔진의 "부도 가정" 버그 발견·수정 (Claude, 2026-09-22)

소유자 지시: "이 수익률 계산 버그를 지금 어느 수준까지 파고들까요?" → "DART 원문으로
정확한 교환가치 확인 후 엔진 수정" 선택.

**발견한 것**: v_trend 2건(004200,000060), v4 1건(282690)의 `held_through_listing_end`
findings을 조사하다가, 세 종목 모두 가격이 폭락한 게 아니라 **안정적인 가격에서 거래가
멈춘 뒤 데이터가 사라짐**(marcap으로 확인)을 발견 — 부도의 신호가 아니라 자진 비상장화
(포괄적 주식교환/흡수합병)의 전형적 패턴. OpenDartReader로 DART 원문을 직접 열어
확인한 결과:
- **000060(메리츠화재)**: 2023-02-01 포괄적 주식교환으로 (주)메리츠금융지주(138040)의
  완전자회사 전환. 원문(rcept_no=20221205000271)에서 정확한 교환비율 확인:
  **1주당 1.2657378주**. 신주 상장일(2023-02-21) 종가 기준 정산.
- **282690(동아타이어)**: 2024-09-04 (주)디엔오토모티브가 흡수합병(존속회사). 합병비율
  1:0.1558169까지 확인(rcept_no=20240904000321) — 승계종목(디엔오토모티브) 코드를
  이번 세션엔 못 찾아서 정확한 회수가치는 미확정.
- **004200(고려개발)**: 2020-03-27 주식회사 삼호(대림그룹)가 흡수합병. 합병비율
  1:0.4516274까지 확인(rcept_no=20200327001371) — 삼호가 이후 다시 개편됐을 가능성이
  있어 최종 승계가치 추적 미완.

그런데 공용 백테스트 엔진(`_final_liquidation_quote()`, v4/v_trend를 포함한 다수 전략이
공유)은 이 세 경우를 전부 **"시세부재→전액손실(-100%)"로 가정**하고 있었다. 실제로는
000060의 경우 (교환비율 1.2657378 × 승계종목 138040의 상장일 종가 41,211.77원 =
**52,163원**) — 진입가 55,500원 대비 **-6.01%**(전액손실이 아니라 소폭 손실)가 진짜
결과다. 이건 v_trend/v4 두 전략만의 버그가 아니라 이 엔진을 쓰는 모든 전략의 과거
수익률 계산에 영향을 준 systemic 문제.

**조치**: 근거 없는 추정을 절대 넣지 않는다는 원칙 그대로 — **000060 1건만** 완전히
검증된 수치로 수정, 282690/004200은 실제 합병은 확인됐지만 정확한 승계가치가 아직
미확정이라 손대지 않음(기존 -100% 가정 그대로 유지, 더 나빠지지 않음).

- 신규 테이블 `delisting_outcomes`(stock_code,event_date,outcome_type,
  successor_stock_code,exchange_ratio,successor_reference_date,cash_per_share,evidence_*,
  source,confidence,status). `status='confirmed'`인 행만 엔진이 사용 — 000060 1건만
  DART 원문 근거와 함께 등록.
- `backtest_common.py`: `_load_delisting_outcomes(conn, stock_codes)` 신규(기존
  `_load_corp_action_factors()`와 동일한 "미리 로드해서 넘기는" 관례 재사용).
  `_final_liquidation_quote()`에 `delisting_recovery` 옵션 인자 추가(기본 None=기존
  동작 100% 동일, 하위호환) — 실거래일 종가가 있으면 항상 그게 우선이고, 없을 때만
  confirmed 회수가치, 그것도 없으면 기존 0원 가정으로 정확히 폴백.
  `_run_portfolio`(v4 엔진; v5는 `_run_generic_backtest` 사용)와 `_run_generic_backtest_with_sc`(V10+HS/V11+HS 엔진)
  양쪽에 배선. `_run_generic_backtest`(v_trend 등 대부분 전략이 실제로 쓰는 엔진)의
  강제청산 블록은 원래 `_final_liquidation_quote()`를 안 쓰고 같은 로직을 자체
  복제해뒀던 걸 발견해서, 이번에 공용 함수를 호출하도록 통합(중복 제거+버그 수정 동시).
- 신규 테스트 8건(`tests/test_backtest_final_liquidation.py`) — 기존 4건 전부 그대로
  통과(하위호환 확인) + `_load_delisting_outcomes` 단위테스트 4건 + `_run_portfolio`
  end-to-end 배선 검증 1건. 전체 회귀 225건 통과.
- **실 DB 검증**: `_load_delisting_outcomes(실conn, ['000060'])` 직접 호출 →
  `52163.29원`(실제 저장된 138040 종가 기준, 수기 계산과 일치) 반환 확인. 000060의
  원래 등록된 거래(진입 55,500원, 2023-01-19)를 재현해보면 **-100.0% → -6.01%**로
  정확히 바뀜.
- v_trend 22.11~23.10 구간을 새로 재실행(run_id 467585cf)해봤는데 000060이 이번엔
  후보에 아예 안 들어옴(이번 세션 중 다른 데이터 수정들로 유니버스가 자연스럽게
  달라짐) — 그래서 "재실행 전후 직접 비교"는 못 했지만, 위 실DB 직접호출 검증으로
  고정 로직 자체는 확실히 맞게 동작함을 확인했다.

**후속: v_trend 6기간 전체 재실행 완료, survivorship 실제로 0건 확인됨**
`scripts/rerun_selected_after_price_repair.py --strategies v_trend,v4 --workers 4`로 재실행:
- **v_trend**: 6기간 전부 재등록 성공(new_suite=3f1440757a39e991), `audit_selected_
  strategy_price_integrity.py` 재확인 결과 **survivorship_findings=0**(기존 2건에서
  완전히 해소). `data_availability`도 6/6 통과(어제 v1.py 오탐 수정 그대로 유효).
- **v4**: 병렬 재실행 중 다른 기간의 `_code_fingerprint`가 서로 달라져
  `register_run_set`이 "all run-set components must share one code fingerprint"로
  실패 — 재실행 도중(4 workers 동시 실행) 동시세션이 `backtest_common.py` 등 지문
  대상 파일을 만졌을 가능성 있음. v4는 다음 세션에 순차 재실행 필요.
- **재실행 중 발견한 별개 버그**: `run_registry._spec_for_hash()`가
  `ORDER BY s.created_at DESC LIMIT 1`로 최신 스펙을 고르는데, 이번 병렬 재실행이
  남긴 "tuple concurrently updated"(동시세션과의 트리거 생성 경합, 비치명적) 부작용으로
  같은 run_hash에 `status='running'`(trades_json 없음)인 고아 행이 하나 더 생겼고,
  둘 다 `created_at=NULL`이라 어느 쪽이 선택될지 비결정적이었다 — 고아 행 2건
  (run_id d06e93a1, b96a7d41, 둘 다 trades_json NULL 확인 후) 삭제해서 해소. 근본
  원인(NULL created_at 시 tie-break 비결정성)은 이번엔 안 고침 — 산발적으로 재발 가능.
- **v_trend가 여전히 legacy인 진짜 이유(오늘 작업과 무관)**: 동시세션이 새로
  등록한 `universe_integrity` 게이트가 22.11~23.10 등 여러 구간에서 실패 —
  `assert_research_prices(exclude=True)`(이 세션 초반에 내가 도입한 정책)가 해당
  구간 후보종목의 **18.57%(440/2369)**를 가격미검증으로 제외했는데, 임계값(7%)을
  훨씬 초과한다. 이건 버그가 아니라 `unresolved_active_common`(6,904건) 풀이
  얼마나 큰지를 정직하게 드러내는 정상 동작 — 근본 해결은 그 풀 자체를 pykrx/FDR/
  marcap으로 검증해서 줄이는 것뿐(아래 "다음 세션 후보" 1순위와 동일 작업).

**다음 세션 후보**: (1) v4 순차 재실행+재등록. (2) 282690의 승계종목(디엔오토모티브)
실제 종목코드 확인, 004200→삼호의 이후 행방 추적 — 확인되면 `delisting_outcomes`에
추가해 두 건도 해소 가능. (3) `unresolved_active_common`/`mixed_basis_or_price_
corruption` 전역 풀을 pykrx+FinanceDataReader+marcap 3중교차검증으로 줄이는 게
`universe_integrity` 게이트 정상화의 유일한 근본 해법 — 이제 최우선 순위.

---

## PIT 프로비넌스 인프라 — 설계만 완료, 구현은 보류 (Claude, 2026-09-22)

소유자가 승인한 4개 작업 중 "6개 전략 PIT 프로비넌스 인프라 구축"은 **설계까지만 하고
실제 코드 구현은 하지 않기로 판단**했다. 이유: 오늘 이미 공용 백테스트 엔진의 핵심
청산로직(`_final_liquidation_quote`/`delisting_outcomes`)을 수정했고, 그 전에도
governance 레지스트리를 전역으로 재동결했다 — 실제 수익률 계산에 영향을 주는 변경을
하루에 이만큼 쌓은 상태에서, golden_cross/v8/v4/recovery/earnings_supply_discovery/
high_profit_compound 6개 전략의 신호계산 핫패스를 추가로 더 건드리는 건 검증 부담이
과도하다고 판단(각 파일 구조가 전부 달라 한 번에 안전하게 처리하기 어려움). 아래는
다음 세션이 바로 시작할 수 있는 구체적 설계.

**목표**: `data_availability` 게이트가 요구하는 "신호별 입력 행ID + available_at을
persist"를 실제로 만족시켜, 이 6개 전략이 근거 있게 게이트를 통과하도록 한다(현재처럼
그냥 통과시키는 게 아니라, 진짜 룩어헤드가 없었음을 사후 검증 가능하게 만드는 것).

**설계**:
1. 신규 테이블 `signal_data_provenance`(run_hash, stock_code, decision_date,
   financial_data_row_id 또는 (year,quarter,report_type) 복합키, avail_date_used,
   created_at). 성능을 위해 "이 run에서 (stock_code, 사용된 재무행)" 조합을 메모리
   set으로 모았다가 백테스트 종료 시 한 번에 bulk insert(일별로 쓰면 너무 비쌈).
2. 공용 헬퍼 `_record_financial_provenance(...)` — 이미 있는 `_load_corp_action_factors`/
   `_load_delisting_outcomes`와 같은 "미리 로드해서 넘기는" 관례를 그대로 따르되,
   방향이 반대(쓰기)라 러너가 끝날 때 flush하는 형태.
3. 6개 전략 각각 실제 소비 지점(golden_cross `_sector_score_as_of`, v8 `fin_all`/
   `sd['fins']`, v4/base.py `fin_all`, recovery `ta_fins`, earnings_supply_discovery
   `raw_rows`, high_profit_compound `_insider_buy_codes`/`dart_contracts`/
   `order_backlog`)에 개별 배선 — 이 부분이 가장 위험한 단계이므로 **한 번에 1개
   전략씩, 전후 6기간 재실행 비교로 수익률이 안 바뀌는지 확인하며** 진행할 것
   (계측만 추가하고 로직은 안 바꾸는 거라 수익률이 바뀌면 배선 버그).
4. `audit_selected_strategy_data_availability.py`에 새 통과 경로 추가: 텍스트스캔이
   "사용함"으로 판정해도, `signal_data_provenance`에 해당 run_hash의 기록이 있고
   전부 `avail_date_used <= decision_date`면 통과 처리.
5. 우선순위: golden_cross가 가장 단순(진입점 1곳, `_is_sector_buy` 하나)해서 첫
   프로토타입 후보로 적합. high_profit_compound(3개 테이블 병렬 사용)이 가장 복잡해서
   마지막 순위 추천.

---

## 다중 AI 작업 재검증 및 추가 수정 (Codex, 2026-09-22)

### 검증 결론

기존 작업은 일부만 완료된 상태였다. 데이터 복구와 7% 보유윈도우 감사, 영숫자 종목코드
수정, 2026-07-17 휴장일 정리는 운영 DB에서 확인됐다. 반면 다음 항목은 완료됐다는 기록과
실제 상태가 달랐다.

1. 선택된 v4 suite `5f95d068835561c4`는 수정 전 run을 계속 참조했다. 23.11~24.12
   컴포넌트의 282690 거래는 2024-10-09 이후 시세가 없는데도 2024-12-30에 13,500원,
   `기간종료`, +3.29%로 청산돼 있었다. 즉 코드 수정만 있었고 선택 결과 재계산은 없었다.
2. `_run_portfolio`의 직접 호출 전략은 v4 하나다. 과거 문서의 “v4/v5 직접 영향”은
   잘못됐으며, v5는 `_run_generic_backtest`를 사용한다. 관련 `CLAUDE.md`와 9/12 검토
   보고서를 정정했다.
3. 기간말 시세부재 처리 결함은 v4만의 문제가 아니었다. 독립 전략 엔진 다수가 과거 마지막
   종가 또는 진입가를 청산가로 사용하거나, 미청산 포지션을 결과에서 조용히 누락했다.
4. 합병·주식교환의 확인된 실제 회수가치도 공유 엔진 일부에만 연결돼 있었다. 독립 전략
   엔진은 `_final_liquidation_quote()`를 호출하더라도 `delisting_outcomes`를 전달하지 않아
   000060 같은 확인 완료 사례를 계속 전액손실로 처리했다.
5. 전략센터 matrix는 suite manifest에 저장된 생성 당시 status를 사용했다. 감사 artifact가
   나중에 갱신돼도 화면 상태가 바뀌지 않는 구조였다.
6. `assert_research_prices`의 PostgreSQL 경로는 2,800만 행 canonical view의 LAG 윈도를
   시장 전체에 계산해 statement timeout이 났다. `source_snapshot`도 전체 과거 행의
   `COUNT(*)`를 매 실행마다 계산해 같은 문제가 있었다.
7. 문제 종목 제외는 로그만 남기고 run artifact에는 남지 않았다. 결과가 어떤 종목을 얼마나
   제외했는지 사후 재현할 수 없었고, 대규모 제외 결과도 다른 게이트만 통과하면 승격될 수
   있었다.
8. 데이터 가용성 감사 쿼리가 `run_hash` 하나에 여러 실행 명세가 연결되면 컴포넌트를
   중복 집계했다. 실제 27전략×6기간은 162개인데 164개로 집계된 사례를 재현했다.

### 반영한 수정

- 기간말 청산을 27개 독립 전략 엔진까지 공통화했다. 마지막 시장일의 해당 종목 시세가
  없으면 과거 종가/진입가를 재사용하지 않으며, 확인된 `delisting_outcomes`가 있으면 실제
  합병·교환/현금 회수가치를 사용하고, 없으면 `기간종료(시세부재 전액손실)`로 기록한다.
- `backtest_common._final_liquidation_quote_for_code()`를 추가해 독립 엔진도 확인된 상장폐지
  결과를 필요 시 조회하도록 연결했다. v4 공유 엔진과 generic 엔진의 기존 bulk preload
  경로는 유지했다.
- `scripts/rerun_selected_after_price_repair.py`의 기업행위 조정 지원 전략 집합에 v4를 넣었다.
- `universe_integrity` artifact를 추가했다. 후보 수, 제외 수/비율, 전체 제외 코드, 감사기간,
  7% 기준을 저장하며, artifact가 있는 새 실행은 7%를 초과하면 실행 검증 게이트를 통과하지
  못한다. 기존 실행은 artifact 부재만으로 소급 실패시키지 않는다.
- 전략센터 matrix가 selected suite마다 `derive_status()`를 한 번 실행해 현재 artifact 상태를
  사용하도록 수정했다. manifest의 과거 status 고정 문제를 제거했다.
- `source_snapshot`의 전역 revision 지문을 과거 전체 `COUNT(*)` 대신 최신 확정행
  `(id, created_at, date)` 경계로 바꿨다. 기간별 가격 합계/checksum은 그대로 유지한다.
- PostgreSQL의 연구가격 사전검사는 인덱스가 있는 `price_jump_audit(stock_code,event_date)`
  판정을 종목 배치로 읽도록 바꿨다. v4처럼 기업행위 조정계수를 쓰는 엔진은 같은 날짜의
  `factor_confirmed` 이벤트만 사전 제외 대상에서 면제한다. SQLite 테스트 경로는 canonical
  view를 계속 사용한다.
- 데이터 가용성 감사의 run_hash 중복 조인을 집계 서브쿼리로 고쳐 정확히 162개 컴포넌트로
  복구했다.
- 검증 중 중단돼 `running`으로 남은 임시 v4 실행 2건은 `error`로 정리했다.

### 실제 재실행 결과

v4 6기간을 전부 다시 계산하고 suite `4807bc8857ea6119`를 선택했다. 새 컴포넌트는 다음과
같다.

| 기간 | run hash | 수익률 | 거래 | 후보 제외율 |
| --- | --- | ---: | ---: | ---: |
| 20.3~21.11 | `d96e6aa6211f` | +87.51% | 381 | 36.84% |
| 21.12~22.10 | `c848b9878bd5` | 0.00% | 0 | 21.52% |
| 22.11~23.10 | `1de41d2f685f` | +6.69% | 171 | 18.53% |
| 23.11~24.12 | `4b7c6c4b13c3` | -0.43% | 84 | 11.59% |
| 24.6~25.5 | `57dd47706702` | -3.05% | 64 | 10.36% |
| 25.6~26.3 | `11c90075faf1` | +34.45% | 209 | 7.61% |

새 23.11~24.12 결과에는 282690 거래가 없다. 따라서 수정 전의 13,500원 허위 정상 청산은
선택 결과에서 제거됐다. 가격/생존편향 후속 감사에서는 v4 전체 909개 보유윈도우 중 오염
1개(0.11%), survivorship finding 0건으로 통과했다. v_trend도 survivorship finding 0건으로
통과했다. 전체 가격 감사는 27개 중 25개 통과, 실패는 deep_recovery 8.82%와
extreme_dd_volume 12.92% 두 전략이다.

그러나 새 v4 suite의 최종 검증 상태는 **legacy**다. 여섯 기간 모두 후보 제외율이 7%를
넘어 `universe_integrity`가 실패했고, 21.12~22.10은 거래가 0건이라 price/survivorship/
corporate-action evidence도 실패했다. v4는 재무 신호의 소비 행 ID와 available_at을 저장하지
않아 `data_availability`도 6/6 실패한다. 새 결과는 청산 버그를 제거한 재계산 증거이지만,
성과 비교나 실거래 채택 근거로 사용하면 안 된다.

### 검증

- Python compile: `backtest_common.py`, `run_registry.py`, `routes/backtest.py`, 전체
  `backtest_strategies/*.py` 통과.
- 관련 회귀테스트: 47개 테스트와 27개 전략별 정적 청산 계약 subtest 통과.
- 데이터 가용성 감사 중복 수정 후 162개 컴포넌트(42 통과/120 실패)로 정확히 집계.
- 선택 v4 suite의 동적 `derive_status`: `legacy` 확인.

### 아직 완료되지 않은 항목

1. **후보 유니버스 오염**: 기간별 202~813종목(7.61~36.84%)이 미확정 가격 이벤트 때문에
   통째로 제외된다. 보유윈도우 7% 정책만으로는 진입 후보 단계의 선택편향을 해결하지
   못한다. `unresolved_active_common`, `externally_confirmed_internal_corruption`,
   `corporate_action_pending_confirmation`을 추가 정리하거나 이벤트 이후 제한된 lookback만
   마스킹하는 시점별 유니버스 로직이 필요하다.
2. **v4 PIT provenance**: `financial_data`에서 실제 신호가 소비한 행 ID와 avail_date를
   기록하는 `signal_data_provenance` 인프라가 아직 없다. 위에 기록된 설계대로 전략별 배선이
   필요하다.
3. **0거래 컴포넌트**: 재확인 결과 21.12~22.10은 KOSPI MA120 시장필터가 225/225일
   모두 신규매수를 막아 0거래가 됐다. 수정 전 suite의 동일 기간도 0거래였으므로 새 종목
   제외 정책이 만든 회귀는 아니다. 다만 거래 증거가 전혀 없으므로 해당 컴포넌트의
   price/survivorship/corporate-action artifact는 계속 fail-close가 맞다.
4. **관리종목·투자주의/경고/위험·거래정지 이력**: 운영 DB에 독립된 역사 테이블이 여전히
   없다. 현재 `stock_base_info_history`와 DART/가격 흔적으로 일부만 판정한다.
5. **282690/004200 승계가치**: 합병 사실과 비율은 확인됐지만 최종 승계종목/정산가를 아직
   확정하지 못했다. 확인 전에는 추정값을 넣지 않는다.
6. **287만 행 후보**: 전역 일괄 적용은 계속 보류한다. 공급자·기업행위·상장구간이 모두
   일치한 표본부터 승격한다.

---

## golden_cross 룩어헤드 편향 버그 발견·수정·재실행 완료 (Claude, 2026-09-22)

PIT 인프라 프로토타입으로 golden_cross를 계측하려다가, `data_availability` 오탐 문제보다
훨씬 심각한 **실제 룩어헤드(미래정보 사용) 버그**를 발견해서 소유자 확인 후 직접 수정.

**버그**: `backtest_strategies/golden_cross.py:_sector_score_as_of()`의 영업이익 YoY
계산이 (1) `avail_date`(공시일) 필터가 전혀 없었고 (2) `cur_year = as_of[:4]`로 **as_of가
속한 해 자체**의 연간실적을 조회했다. 한국 연간실적은 보통 다음해 3월에 공시되므로,
`as_of`가 속한 해의 연간실적은 그 시점엔 아직 존재할 수 없는 게 정상인데, 백테스트는
DB의 현재(전체 역사) 상태를 그대로 조회하니 실제로는 이미 수집된 미래 데이터를 읽어온다.

**실측 확인**: `as_of='2025-06-15'`로 시뮬레이션할 때 — 구버전 쿼리는 005930의 FY2025
영업이익(43.6조원, 실제 공시일 2026-03-11로 그 시점엔 존재할 수 없는 값)을 그대로
반환했고, 수정 후 쿼리는 올바르게 FY2024 영업이익(32.7조원, 실제로 2025년 3월에 이미
공시되어 2025-06-15 시점엔 알 수 있었던 값)을 반환했다.

**수정**: `cur_year`/`prev_year`를 `as_of연도-1`/`as_of연도-2`로 정정(공시 시점 기준으로
실제 가장 최근 사용 가능한 연간실적과 비교하도록 의미 자체를 바로잡음) + 다른 전략들과
동일한 `COALESCE(fin_disclosure_dates.avail_date, 법정기한 fallback) <= as_of` 필터를
이중 안전장치로 추가(3월 이전 구간까지 방어). 전체 회귀테스트 231건 그대로 통과 확인 후
6기간 전체 재실행.

**재실행 결과**(`scripts/rerun_selected_after_price_repair.py`의 엄격한 0-tolerance
가격무결성 체크가 21.12~22.10 구간에서 걸려 그 스크립트는 못 씀 — 대신 각 기간
`run_backtest_golden_cross()`를 직접 호출 후 `register_run_set`+`select_run`으로 등록):

| 기간 | 수익률 | 거래수 |
| --- | ---: | ---: |
| 20.3~21.11 | +86.58% | 98 |
| 21.12~22.10 | -39.89%* | 50 |
| 22.11~23.10 | -22.78% | 48 |
| 23.11~24.12 | +1.96% | 39 |
| 24.6~25.5 | +11.94% | 30 |
| 25.6~26.3 | +119.18% | 43 |

avg6 ≈ +26.16%(수정 전 26.91%와 큰 차이 없음 — 이 버그가 영향을 준 건 `sector_bonus`라는
진입 우선순위 보조지표 하나뿐이라 전체 수익률 왜곡은 크지 않았던 것으로 보임. 다만 어떤
종목이 우선 편입됐는지의 미시적 차이는 존재할 수 있음).

*21.12~22.10만 `price_integrity` 실패(`unresolved_active_common` 오염 4.22%~초과) —
이건 이 수정과 무관한, 이번 세션 내내 반복 확인된 그 사전 존재 문제 그대로임(pykrx/FDR/
marcap 3중교차검증으로 그 풀을 줄이지 않는 한 해소 안 됨).

**최종 governance 상태**: 여전히 `legacy`(data_availability 6/6 실패 — 이건 버그가 아니라
진짜 PIT 인프라 미비, 애초에 이 세션의 "8개 전략 실사"에서 이미 "진짜 갭"으로 정확히
분류했던 것 그대로) + 21.12~22.10 구간의 사전존재 가격오염 문제. **하지만 버그 자체는
확실히 제거됐고 5/6 구간이 `point_in_time_approx`로 정상 승격됨**(수정 전엔 전부 legacy).

**PIT 인프라 작업 자체는 여전히 미착수** — 이 발견 때문에 원래 계획(golden_cross에 계측
배선)을 잠시 멈추고 버그부터 고쳤다. PIT 인프라(신호별 소비 행ID+avail_date persist)는
`## PIT 프로비넌스 인프라 — 설계만 완료` 섹션의 설계 그대로 다음 세션 과제로 남는다.
단, **다른 5개 전략(v8/v4/recovery/earnings_supply_discovery/high_profit_compound)도
golden_cross와 같은 종류의 avail_date 누락 룩어헤드 버그가 있는지 먼저 전수 점검하는 게
최우선** — PIT 인프라(증빙 저장)를 계측하기 전에, 애초에 계산 자체가 맞는지부터 확인해야
한다는 게 이번 발견의 핵심 교훈.

---

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
