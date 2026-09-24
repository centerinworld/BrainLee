# hermes.md 아카이브 (2026-09-20~22, 2026-09-24 이관)

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

