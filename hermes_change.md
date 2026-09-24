# hermes_change.md — Stock Dashboard 변경 로그 (Hermes 작업 기록)

> 🪙 **토큰 최적화(2026-09-24)**: 2026-09-23 이전 변경 로그는 [docs/hermes_change_archive_20260919-21.md](docs/hermes_change_archive_20260919-21.md)로 이관. 새 변경은 맨 아래에 5~10줄 요약으로 추가하고 2주 지난 항목은 아카이브로 이동.
> **대상 프로젝트**: `/Volumes/Realtek_NVME/stock_dashboard/runtime/` (운영본, PostgreSQL)
> **규칙**: 모든 코드/설정 변경은 반드시 이 파일에 기록.

---

## [2026-09-23 code-doer] "수치 불일치" 원인 규명 — API 500(비-sargable 날짜 술어) 수정 + 데이터 계층 결함 정량화
### A. 수정 완료 (코드, 실측 검증)
- 대상: `routes/market_indicators.py` (working-tree `M`, sha256 `9dc089d3c36e23fd…`)
- 원인: `substr(<t>.date,1,10)` 등가/비교 술어와 `strftime('%w', date)` 요일 필터는 PG에서
  sargable하지 않다 → `ix_price_history_6755ae263c(date)` / `ix_price_history_0b57a1f65a(stock_code,date)`
  미사용 → 1,024만행(2.9GB) seq scan(로그 wait_event `IO:DataFileRead`로 실측) → 30초
  statement_timeout → `psycopg.errors.QueryCanceled` → HTTP 500
  (재현: /api/market-indicators/investor-top · turnover-top · available-dates, backend.launchd.log).
- 수정: `_day_bounds()`([당일, 익일) 범위) 헬퍼 신설, 날짜 등가/비교 술어 전량 범위 술어로 교체,
  available-dates는 limit 비례 인덱스 스캔 창 + 파이썬 요일 필터로 전환, market-summary에 400일 창 추가.
- 실측(라이브 PG, 동일 함수 in-process + 별도 ASGI 하네스 HTTP): available-dates 6.10s→0.09s,
  investor-top 5.10s→0.01s, turnover-top 20.30s→0.19s, market-summary 0.01s→0.003s.
  시장지표 9개 엔드포인트 전부 정상.
- 추가 최적화(checker 지적 반영): turnover-top의 전일종가 조회가 `ph.date = (SELECT MAX(ph2.date)…)`
  상관 서브쿼리를 함께 걸어 후보 행마다 MAX를 재평가 → 20종목에 1,189ms. `ORDER BY date DESC` +
  DISTINCT ON만으로 결과가 완전 동일(사전/사후 dict 비교 True)하면서 172ms로 감소.
  엔드포인트 전체 1.44s→0.26s. 이 잔여 시간은 파이썬 오버헤드가 아니라 서버측 SQL 시간
  (cProfile: psycopg connection.wait() 1.27s / connect() 33ms).
- 등가성 증명: `SELECT count(*) FROM price_history WHERE substr(date,1,10) <> date` = **0**
  (라이브 1,024만행) → 범위 술어는 substr 술어와 정확히 같은 행을 고른다. 일자별 카운트 12일 대조 일치.
- 회귀 테스트: `tests/test_market_indicators_date_predicates.py` (5 passed, 읽기전용).
- ⚠️ 미반영: 라이브 uvicorn(pid 4428)은 재시작 전까지 구 코드를 서빙 → 재시작 필요(사람 승인 대기).

### B. 데이터 계층 결함 — 실제 "수치 불일치"의 본체 (미수정, 승인 대기)
1. 종목 커버리지 결손: 9/14~9/18 일별 350~365종목(정상 2,700종목의 13%), 9/23은 682종목.
   `scripts/backfill_price_fdr.py` DRY-RUN 재실측 = 결측 11,638건(9/14 2,321 … 9/18 2,331).
   동일 스크립트 대조: 보유 40종목 중 25건이 정산종가와 0.1~4% 불일치(장중 스냅샷 잔존).
   → 그 날짜의 순매수·회전율·합계가 정상일 대비 약 8배 작게/다르게 표시된다.
2. 지수(^KS11/^KQ11) 수급 컬럼이 9/11부터 전부 0 (마지막 정상 9/10) → market-summary·index-investor는
   KOSPI 수급 0을, 시장시그널 supply_flow는 "수급 데이터 수집 대기"(gray)를 표시. 반면 전종목 합계
   기반 investor-trend는 다른 값을 표시 → 같은 페이지 안에서 값이 갈린다.
3. 기준일 불일치: `_latest_trade_date()`(종목 ≥2,000 기준)는 9/23(682종목)을 건너뛰고 9/22를 반환하나
   market-summary·available-dates는 9/23을 반환 → 같은 화면에서 기준일이 9/22·9/23으로 갈린다.
- 착수 전제: A의 수정은 라이브 재시작 승인 후 HTTP로 재검증, B의 결측 복구는 `--apply` 승인 후 실행.
- ⚠️ 복구 도구 정정(2026-09-23 추가 조사): `scripts/backfill_price_fdr.py --apply`는 **무효(no-op)** 다.
  쓰기 가드가 활성인데(`current_setting('app.price_basis_checked',true)` = NULL; 세션/DB/롤 설정 없음)
  이 스크립트는 `gate_price_batch`도 `set_config('app.price_basis_checked','1')`도 호출하지 않고,
  행 단위 예외를 삼켜 `skipped`로 집계한다. 실측: `INSERT INTO price_history('005930','2026-09-17',…)`
  → `ERROR: price integrity guard blocked unverified INSERT for 005930 on 2026-09-17`
  ⇒ 승인해도 `inserted=0 skipped=11,638`으로 "실행된 것처럼" 보이고 데이터는 그대로다.
- 대체 정본 도구: `scripts/recover_price_fdr.py`(동일 9/14~9/18 기본 창) — ① `price_history_fix_backup`
  (old/new 전량, 현재 297,116행 실재) ② `data_fix_log`(source='FDR_verified'/'FDR_backfill', fixed_at)
  ③ 삽입 전 `gate_gap_fill_row` 가격제한폭 경계검증 ④ 사후 재-diff 0건. 가드 우회 없이 set_config로 통과.
- 롤백: `run_id` 기준 `price_history_fix_backup`의 old/new로 원복(수정분 UPDATE, 삽입분 old_close IS NULL → DELETE).
- 참고: 9/14~9/18 결손·정산가 불일치의 루트원인(9/12경 야간 전종목 확정수집 중단)은 2026-09-21 항목에 기재됨.

---

## [2026-09-24 code-doer] 검증봇 요청 PostgreSQL cutover repair 패키지 — 실결함 1건 수정 + cutover verifier 통과

ACTIVE 트리(`/Volumes/Realtek_NVME/stock_dashboard/runtime`)만 수정. dirty worktree의 무관 변경은 보존.

### A. 요청 10항목 중 9항목은 이미 반영되어 있었다 (재구현 없이 실측 검증만)
1. **DATE 파라미터 offset**: `db_compat.py`가 `DATE(?, '+30 days')`/`DATE(?, '-10 days')`를 `?::date ± INTERVAL 'N days'`로 변환. `GET /api/dart-contracts/20260918800219` = **200**(이전 500), 4,529바이트 실데이터 반환.
2. **named mapping 파라미터**: `_replace_named_placeholders`(`:name`→`%(name)s`, `::cast`·리터럴 보존) + `_execute_named` dict 경로 + `executemany` dict 지원.
3. **`json_extract`**: `jsonb_path_query_first((expr)::jsonb, (path)::jsonpath) #>> '{}'`(paren-balanced 토크나이저). TEXT/JSONB·중첩/단순 경로 모두 동작.
4. **`is_annual IS [NOT] TRUE/FALSE`**: `(x::text IN ('0','false','f')) IS TRUE` — SQLite 3값 논리와 일치(NULL은 TRUE/FALSE 둘 다 아님).
5. **`connect_primary_db(readonly=, timeout=)`**: `SET SESSION read only` / `statement_timeout`. 기본(쓰기 가능) 계약 유지.
6. **percent 이스케이프**: 실결함 발견·수정(B).
7. **sqlite_recovery 스크립트 6개**: 전부 `connect_recovery_sqlite_db()`로 실제 SQLite 접속 확인 — `restore_order_backlog_v3_backfill`, `verify_order_backlog_v3_cutover`, `backfill_order_backlog_outliers_v3`, `cleanup_order_backlog_year_captures`, `quarantine_order_backlog_low_confidence`, `verify_tenbagger_postgres`. (apply/mutation 모드 미실행)
8. **`collectors/us_biotech_pipeline_collector.py`**: 사용되지 않던 독립 US SQLite 설정(`US_DB_PATH`)은 이미 제거됨. `_connect()`는 primary PostgreSQL(`connect_primary_db(timeout=120)`) 사용 — side DB는 실재하지 않으므로 구성 제거가 정답.
9. **backtest PG 트리거 + scheduler WAL 가드**: `_ensure_backtest_pg_triggers`(모듈 플래그 1회, `conn.commit()`) 및 `IS_POSTGRES` WAL 가드 확인.

### B. 수정 — `db_compat._escape_literal_percent`가 문자열 리터럴 내 `%b`/`%s`/`%t`를 psycopg placeholder로 오인 (execute·executemany 공통 실결함)
- **증상(수정 전 실측)**: `SELECT 1 WHERE 'abc' LIKE '%b%' AND ? = ?` → psycopg가 `'%b%%'`를 placeholder로 계산해 `the query has 3 placeholders but 2 parameters were passed`. `executemany`에서도 `2 placeholders but 1 parameters`.
- **근본원인**: `%` 다음 글자가 `{s,b,t,%}`이면 문자열 리터럴 내부여도 무조건 placeholder로 보존 → `'%b%'`/`'%s%'`/`'%t%'` LIKE 리터럴이 깨짐.
- **수정**: 문자열 리터럴(`'`/`"`, `''` 이중 인용 포함) 상태를 추적해 **리터럴 내부 `%`는 항상 `%%`**, 리터럴 밖에서는 우리가 생성하는 `%s`·`%(name)s`·`%%`만 보존. `%b`/`%t`는 더 이상 placeholder로 취급하지 않음(우리 변환기는 `%s`/`%(name)s`만 생성).
- **검증**: `'%b%'`→`'%%b%%'`, `'%s%'`→`'%%s%%'`, `'%t%'`→`'%%t%%'`, `'50%%'`→`'50%%%%'`, `x=%s`→`x=%s`, `x=%(n)s`→`x=%(n)s`. execute·executemany 모두 통과.
- **부수 정정**: `PostgresCompatConnection.execute/executemany` 타입 시그니처를 dict 매핑 허용(`Sequence[Any] | dict`)으로 확장해 커서와 일치시킴.

### C. cutover verifier 통과(요청 10) — `cafe_stock_indicator_mappings`를 PG-authoritative로 분류
- **조사**: `ok=false`. 유일 실패 = `cafe_stock_indicator_mappings`(SQLite 1,618 vs PG 1,399, -219). 이전 턴(2026-09-20)에는 `ok=true`였고, 그 사이 4일간 스케줄 잡이 테이블을 재구축해 발생한 드리프트다(내 테스트/코드 변경과 무관 — tests/는 cafe를 참조하지 않음).
- **실측 근거(라이브)**: PG `MAX(updated_at)` = **2026-09-24 07:40:05**(당일), SQLite 스냅샷 = **2026-08-10 07:40:36**(45일 전). PG에만 있는 자연키 32개 / SQLite에만 있는 키 251개.
- `scripts/ops/sync_cafe_stock_indicator_mappings.py`는 `DELETE FROM cafe_stock_indicator_mappings` 후 재삽입하는 **전량 재구축**을 스케줄러(주간 07:10/월간 07:15)로 수행 → **PostgreSQL이 유일 권위 원천**.
- 따라서 SQLite→PG 동기화는 45일 스테일 값을 되돌려 넣는 **파괴적 덮어쓰기**(요청에서 금지). 대신 verifier에 이미 설계된 `POSTGRES_AUTHORITATIVE_SNAPSHOTS` 메커니즘(행수 대신 신선도 비교)에 `"cafe_stock_indicator_mappings": "updated_at"`을 추가 — PG(2026-09-24) ≥ SQLite(2026-08-10).
- **결과**: `scripts/verify_postgres_cutover.py` → **`ok=true`**, `failures=[]`, `missing_tables=[]`, `postgres_behind=[]`.
- ⚠️ **리뷰 요청**: 이 변경은 검증 게이트의 테이블 분류를 바꾼다(데이터 쓰기 0건). 근거는 위 실측이며 판단이 다르면 되돌릴 수 있다. `dart_insider_holdings` 자연키 결손 0건은 그대로다.

### D. 테스트 / 검증 (실행 증적)
- **신규** `tests/test_db_compat_binding_and_guards.py` 11건: percent 이스케이프 단위 5 + 바인딩 통합 3(execute/executemany LIKE '%b%', executemany dict) + `is_annual` vs 실 SQLite 3값 논리 1 + 접속 계약(readonly/timeout/쓰기 기본) 2. **11/11 OK**.
- **전체 스위트**: `venv/bin/python -m unittest discover -s tests -p 'test_*.py'` → **Ran 249 tests ... OK**.
- **py_compile**: `db_compat.py`, `scripts/verify_postgres_cutover.py` 등 통과.
- **서버 재기동**: `bash scripts/safe_restart_backend.sh` (PID 31522 → 93984, 고아 없음, HTTP 200).
- **HTTP GET(재기동 후)**: `/openapi.json` 200, `/api/dashboard/stats` 200(`db_path=postgresql`, last_update `2026-09-24 11:01:19`), `/api/dart-contracts/20260918800219` **200**, `/api/market-radar/semiconductor/financials?type=quarterly` 200, `/api/global-macro/events` 200.
- **미실행(승인/한도 대기)**: DART 재조회(cf_validation_flags 잔여, 키 `020`), `reconcile_postgres_cutover.py --apply`.

### 파일
- 수정: `db_compat.py`(percent 이스케이프 문자열 리터럴 인식 + 연결 시그니처), `scripts/verify_postgres_cutover.py`(cafe PG-authoritative 분류)
- 신규: `tests/test_db_compat_binding_and_guards.py`
