# hermes_change.md 아카이브 (2026-09-19~21, 2026-09-24 이관)

# hermes_change.md — Stock Dashboard 변경 로그 (Hermes 작업 기록)

> **목적**: Claude/GPT가 이 프로젝트의 Hermes 작업을 검토할 수 있도록 모든 변경사항을 기록.
> **대상 프로젝트**: `/Volumes/Realtek_NVME/stock_dashboard/runtime/` (실제 구동본, PostgreSQL)
> **규칙**: 모든 코드/설정 변경은 반드시 이 파일에 기록 후 진행.

---

## 2026-09-20 — Claude 재검토 반영 및 운영 재검증

### 수정 완료
- recovery/cutover 검증 스크립트는 `connect_recovery_sqlite_db()`로 원본 `stock.db`를 명시적으로 열도록 수정했다. PostgreSQL primary와 SQLite recovery를 자기비교하던 오류를 제거했다.
- `DATE(?, '+N days')`를 PostgreSQL interval 식으로 변환하도록 보완했다. DART 계약 상세 경로의 향후 가격 조회에 적용된다.
- nullable `is_annual IS [NOT] TRUE/FALSE` 변환은 SQLite의 NULL truth table을 보존하도록 수정했다.
- `json_extract()`는 TEXT JSON과 동적 JSONPath를 지원하도록 `jsonb_path_query_first(...::jsonb, ...::jsonpath)`로 변환한다.
- `executemany()`도 `execute()`와 동일한 literal `%` 및 named parameter 처리 규칙을 사용한다.
- PostgreSQL primary 연결은 `readonly`와 `timeout`을 실제 session read-only/statement timeout에 반영한다.
- US biotech pipeline은 PostgreSQL primary 데이터이며, 사용되지 않는 독립 US SQLite 설정을 제거했다.

### 검증 완료
- `tests/test_db_compat_regressions.py`: 4 tests passed.
- live DB: recovery SQLite `financial_data=191,939`, primary PostgreSQL `financial_data=200,798`; named parameter, `DATE` 양/음수 offset, JSON extract, batch literal `%`, read-only (`transaction_read_only=on`) 및 `statement_timeout=500ms`를 확인했다.
- LaunchAgent `com.stock-dashboard.local` 재기동 후 `GET /api/dart-contracts/20260918800219`, `GET /api/trend/v18/recommendations`, `GET /api/contract-advance-signals/top` 모두 HTTP 200.

### 아직 완료로 선언하지 않는 항목
- `verify_postgres_cutover.py`의 missing/behind 테이블은 테이블별 reconciliation과 원본 데이터 보강이 필요하다.
- tenbagger의 security interval 위반 및 전체 cutover parity는 recovery SQLite 자기비교 수정 후 별도 재검증이 필요하다.

---

## 2026-09-20 — DART bridge NULL-key 재검증 및 수정

### 수정 완료
- `scripts/sync_sqlite_bridge_delta.py`는 nullable 자연 conflict key를 가진 테이블을 이제 동기화 전에 거부한다. PostgreSQL의 일반 UNIQUE index는 NULL을 서로 충돌하지 않는 값으로 취급하므로, 이런 행을 `ON CONFLICT`로 upsert하면 매 실행마다 중복 적재될 수 있다.
- `dart_insider_holdings`에서 bridge가 중복 적재한 nullable-key 후발 행 28,322개를 제거했다. 삭제 행은 `dart_insider_holdings_null_bridge_backup_20260920`에 보관했다.
- `scripts/verify_postgres_cutover.py`의 DART 제약 예외 설명을 현재 상태에 맞게 갱신했다.
- 이전 번역 테스트의 `is_annual` NULL 의미 및 `json_extract` 기대값을 실제 호환 규칙에 맞게 수정하고, bridge nullable-key 안전성 테스트를 추가했다.

### 검증 완료
- PostgreSQL `dart_insider_holdings`: 62,059행, 자연키 중복 그룹 0, unique index `ix_dart_insider_holdings_nk` 존재.
- recovery SQLite는 `sp_stock_lmp_cnt` nullable 자연키를 포함하며, bridge는 `refusing unsafe bridge merge`로 정상 차단된다.
- `GET /api/dart-contracts/20260918800219`: HTTP 200.
- compatibility/bridge 회귀 테스트 16건 통과.

### 남은 항목
- `verify_postgres_cutover.py` 재실행 결과 전체 cutover는 아직 `ok=false`다. PostgreSQL에 `company_product_mix`, `gems_analyst_insights`, `gems_daily_learned_logs`가 없고, `cafe_stock_indicator_mappings`는 SQLite보다 213행, `dart_insider_holdings`는 16,522행 부족하다.
- PostgreSQL과 recovery SQLite 간 DART 행수 차이 및 전체 cutover row drift는 자동 bridge 대상이 아니다. 테이블별 authoritative source, NULL-key 병합 기준, freshness 기준을 정한 reconciliation 작업으로 별도 처리한다.

---

## 2026-09-20 — FIN_CROSS 현재값 재검증

- `/tmp/fin_cross_recheck.json`의 917개 DART 연간 재조회 결과를 현재 PostgreSQL `financial_data`와 다시 대조했다.
- 현재 값이 DART CFS/OFS와 일치하고 FnGuide만 다른 409건을 `STRUCTURAL`로 확정했다. `financial_data`는 변경하지 않았다.
- 현재 값도 DART 원문과 일치하지 않거나 대상 행이 없는 508건은 `AMBIGUOUS`로 유지했다.
- 근거 산출물: `research_outputs/fin_cross_reconciliation_20260920.json`. 현재 전체 `AMBIGUOUS=1,066`, FIN_CROSS `AMBIGUOUS=508`.
- FIN_CROSS 매출 338건을 동일 CFS/OFS 기준의 현재 DART 행으로 추가 대조했다. 218건을 값 변경 없이 `STRUCTURAL`로 확정했고, 매출 미결은 120건이다. 근거: `research_outputs/fin_cross_revenue_reconciliation_20260920.json`; 전체 `AMBIGUOUS=848`.
- 매출 미결 120건 중 현재 DART 연간 행이 live DART와 동일 CFS/OFS 기준에서 직접 불일치한 89건은 `financial_data.revenue`를 live 값으로 정정하고 `STRUCTURAL`로 확정했다. 근거: `research_outputs/fin_cross_revenue_dart_corrections_20260920.json`; 전체 `AMBIGUOUS=759`, 매출 미결 31건.
- FIN_CROSS 총자산·총자본 104건 중 동일 CFS/OFS 기준의 다른 현재 DART 행이 live 원문과 일치한 83건을 값 변경 없이 `STRUCTURAL`로 확정했다. 근거: `research_outputs/fin_cross_balance_sheet_reconciliation_20260920.json`; 전체 `AMBIGUOUS=676`, 해당 BS 미결 21건.
- FIN_CROSS 영업이익·순이익 66건 중 동일 CFS/OFS 기준의 다른 현재 DART 행이 live 원문과 일치한 46건을 값 변경 없이 `STRUCTURAL`로 확정했다. 직접 불일치 후보에는 외화보고·단위 차이 또는 재수집 파싱 이상 가능성이 있어 값 수정 없이 보류했다. 근거: `research_outputs/fin_cross_profit_reconciliation_20260920.json`; 전체 `AMBIGUOUS=630`, 해당 손익 미결 20건.
- 현금흐름 3중검증의 `get_cf_values()`가 OFS가 먼저 삽입된 경우에도 해당 값을 읽을 수 있던 결함을 수정해 CFS 우선 정렬을 강제했다. 기존 미결 558건을 당시 FnGuide·Seibro 스냅샷과 현재 CFS 값으로 재판정해 `CONFIRMED` 224건, `CLOSE_MATCH` 3건을 플래그만 갱신했다. 현금흐름 원본값(`cash_flow_data`)은 수정하지 않았다. 근거: `research_outputs/cf_basis_reclassification_20260920.json`; 전체 `AMBIGUOUS=403`, CF 미결 331건(영업CF 118·투자CF 140·기말현금 73건).
- 남은 CF 331건의 DART 연간 공시 원문 재조회 도구(`scratch/recheck_cf_ambiguous_dart.py`)를 만들었으나, 실행 시 등록된 DART API 키 3개가 모두 첫 호출부터 한도 초과(`020`)를 반환해 증적 파일 생성에 실패했다. DB 변경은 없었으며, 매 25건 체크포인트 저장과 `--resume` 재개 기능을 추가했다. 키 한도 회복 후에만 원문 대조를 재개한다.
- standalone 검토 중 수집·검증이 의도치 않게 실행되는 부수 효과를 차단했다. `collectors/fred_collector.py`는 이제 `--collect`가 있어야 DB 수집을 수행하며, `scripts/verify_tenbagger_postgres.py`는 argparse를 통해 `--help`를 처리한 뒤 검증기를 실행한다. `--help` 2건이 종료 코드 0이고 tenbagger 결과 파일의 수정 시간이 바뀌지 않음을 확인했다.
- cutover reconciliation에서 `company_product_mix` 14,482건과 gems 2개 테이블(2건·45건)을 PostgreSQL에 생성·복제하고, `cafe_stock_indicator_mappings`를 자연키로 upsert했다. `dart_insider_holdings`의 SQLite surrogate id 결손 16,563건은 기존 PostgreSQL 자연키와 충돌한다. 중복 적재를 피하기 위해 강제 행수 맞춤은 하지 않았고, 후속 의미값 기반 natural key 검증으로 실제 누락이 없음을 확인해 8번을 완료 처리했다.
- 서버 LaunchAgent 재기동 후 `/openapi.json` 및 `/api/dashboard/stats` cold-path는 정상(대시보드 `db_path=postgresql`)이었다. 기존 `/api/semiconductor/financials` 경로는 현재 404로 확인돼 이 경로를 포함한 전체 cold-path 완료 판정은 보류한다.
- `routes/market_radar.py` 반도체 분기 재무 API 500을 수정했다. PostgreSQL boolean `is_annual`에 0/1 파라미터를 비교하던 쿼리를 `IS TRUE`/`IS FALSE`로 바꾸고, numeric(`Decimal`) 값을 float 상수로 나누던 코드를 정수 단위로 변경했다. LaunchAgent 재기동 후 `/api/dashboard/financial-table/005930` 및 `/api/market-radar/semiconductor/financials?type=quarterly`가 정상 응답했다. **10번 cold-path 검증 완료.**
- `dart_insider_holdings` reconciliation 완료: SQLite의 쉼표·과학적 표기 숫자를 `Decimal` 의미값으로 정규화해 natural key를 비교하도록 reconciliation과 verifier를 수정했다. SQLite 62,044개 논리키가 PostgreSQL 62,059개에 모두 포함됨(`missing_keys=0`)을 확인했고, missing/behind 테이블 없이 `verify_postgres_cutover.py ok=true`를 통과했다. **8번 완료.**
- 2026-09-20 재점검: cutover 완료는 전체 주식 데이터 무결점 선언과 다르다. 현재 `cf_validation_flags` 미결 403건, `QUARTERLY_4WAY` OPEN 5,309건, 가격 감사 `unresolved_active_common` 6,904건과 `mixed_basis_or_price_corruption` 626건이 남아 있다. DART API는 단건 읽기 요청도 `020` 한도 초과여서 근거 없는 값 보정은 보류했다. 다만 `scripts/data_integrity_followup.py`가 동일한 희석률 보정 26건을 매일 다시 UPDATE해 출처 문자열을 중복 누적하던 결함을 고쳤고, 기존 반복 출처 31건을 값 변경 없이 정규화했다. 매출 추출기도 `포괄손익계산서`를 인식하도록 보강했다.
- DART KEY3가 실제 사용 가능했지만 재수집기의 키 순환이 KEY2에서 중단하던 결함을 수정해 CF 원문을 재조회했다. CFS 원문 43건 중 현재값과 일치한 38건은 플래그만 `CONFIRMED`로 처리했고, 불일치 5건은 DART 출처가 명시된 연간 CFS 6행만 원문값으로 정정했다(`cashflow_fix_log` 기록). `cf_validation_flags` AMBIGUOUS는 **403→360건**으로 감소했다.
- 잔여 `cf_validation_flags`는 **299건**을 증적 상태별로 분리해 `research_outputs/remaining_financial_integrity_20260920.json`에 기록했다. DART 현금흐름 원문 응답 없음 268건, DART 연간 원문 응답 없음 25건, 외화 단위·계정 해석이 필요한 FIN_CROSS 6건이다. 이들은 원문 근거 없이 값을 바꾸지 않으며, 재조회 응답이 생길 때 해당 목록의 id부터 재개한다.
- 전체 무결성 큐의 완료·미완료·자동 수정 금지 사유는 새 `hermes.md`에 현재 DB 수치와 재개 근거 파일까지 포함해 정리했다. `hermes.md`를 이 remediation의 최종 상태 문서로 사용한다.

---

## 2026-09-19 — SQLite→PostgreSQL 마이그레이션 현황 조사 (Read-only, 코드 변경 없음)

### 조사 목적
사용자 요청: "raw sqlite3 레이어를 PostgreSQL로 완전 이전(674개 파일)". 변경 전 계획 수립 + 교차검증.

### 핵심 발견 (결론이 뒤집힘)
**마이그레이션은 이미 사실상 완료되어 있음.** 단순 `sqlite3.connect` 호출 수(674)는 오해를 불렀고, 실제로는:

1. **`db_compat.py` (777줄)** — 완전한 SQLite→Postgres 호환 shim:
   - `translate_sqlite_sql()`: SQLite SQL → Postgres SQL 자동 변환
     (INSERT OR IGNORE→ON CONFLICT DO NOTHING, INSERT OR REPLACE→ON CONFLICT DO UPDATE,
      GLOB→~정규식, strftime/date/datetime→TO_CHAR/CURRENT_DATE/EXTRACT,
      GROUP_CONCAT→STRING_AGG, ROUND(x/1e8,n)→ROUND(::numeric,n), julianday→날짜차감,
      instr→STRPOS, printf→||결합, sqlite_master→information_schema,
      INTEGER PRIMARY KEY AUTOINCREMENT→BIGINT IDENTITY, is_annual=0/1→boolean-text 비교)
   - `PostgresCompatConnection`/`PostgresCompatCursor`: sqlite3 인터페이스 모방
     (execute/executemany/fetchone/fetchall/row_factory/lastrowid(RETURNING)/PRAGMA table_info→information_schema)
   - `install_sqlite_primary_router()`: `sqlite3.connect` 전역 monkey-patch —
     **정식 stock.db 경로를 여는 모든 호출을 Postgres로 투명 라우팅**
2. **라우터는 실제로 설치됨**: `main.py:18-21` (모듈 로드 시) + `runtime_pg_bootstrap/sitecustomize.py` (단독 스크립트용)
3. **핵심 모듈은 이미 이전됨**: `signal_engine.py`, `scheduler.py`, `routes/*`, `tenbagger_engine.py`,
   `processor.py`, `crud.py` 등은 `connect_primary_db()`/`connect_stock_db()` 사용.

### 남은 실제 작업 (계획)
| # | 작업 | 범위 | 위험 |
|---|------|------|------|
| 1 | **교차검증**: 정식 stock.db 경로가 라우터를 벗어나는 활성 코드가 없는지 감사 | 조사 | 없음 |
| 2 | **SQL 변환 완전성 검증**: 활성 코드에서 `translate_sqlite_sql()`이 처리 못하는 SQLite 패턴 확인 | 조사 | 없음 |
| 3 | **사장된 경로 정리**: `/Applications/stock_dashboard/stock.db` 27건 (scripts/archive, worktree — dead code) | 수정(소규모) | 매우 낮음 |
| 4 | (선택) `sqlite3.connect(정식경로)` → `connect_primary_db()` 명시적 전환 — 라우터 의존성 제거 | 수정(대규모·기계적) | 낮음 |

### 독립 SQLite DB (정상적으로 분리 유지해야 함 — 이전 대상 아님)
- `employment_monitor/employment.db` (EMP_DB)
- `ETF_check/etf_check.db`
- `Sector_define/sector_april.db`, `sector_followup.db`
- `hs_trade_lab/data/hs_trade_lab.db` (HS_DB)
- US 주식 DB (`US_STOCK_DB_PATH`)
- `:memory:` (테스트)

### 배포 현황 (확인됨)
- 구동 서버: `uvicorn main:app --port 8000` (PID 36957), cwd=`runtime/`, 브랜치 `claude/sqlite-migration-completion-x0h891`
- PostgreSQL 16: `localhost:5432`, db `stock_dashboard`, 397개 테이블, price_history 1,024만 행
- root `/Volumes/Realtek_NVME/stock_dashboard/` = 구형 SQLite 브랜치(`automate-stock-discovery`), 비구동
- `/Users/brainlee` = 사장된 스냅샷

---

## 교차검증 결과 (2026-09-19, 2개 하위 에이전트 독립 감사)

### 감사 1 — 라우터 커버리지 갭 (실제 발산 버그)
**발견: 상대경로 `DB_PATH = "stock.db"` 류가 cwd 의존적이라 라우터를 벗어남** (`_is_primary_sqlite_path`는 상대경로를 `Path.cwd()` 기준으로 해석).
- collectors/ (15개): fred_collector.py:11, eia_oil_supply_collector.py:17, world_bank_collector.py:11,
  oecd_cli_collector.py:17, yahoo_macro_collector.py:10, imf_weo_collector.py:14, reb_housing_collector.py:19,
  fao_food_price_collector.py:18, ecos_collector.py:11, kosis_collector.py:14,
  global_macro_event_collector.py:17, global_macro_event_reaction_collector.py:15,
  global_financial_conditions_collector.py:24, market_quant_bridge_collector.py:16, dram_spot_collector.py:22
- hankyung_consensus_collector.py:219, us_biotech_pipeline_collector.py:23
- routes/global_macro.py:16, routes/sector_rotation.py:14, routes/extra_signals.py:16
- scheduler.py:5154 (_job_wal_daily_check — WAL/VACUUM 잡, Postgres에서 PRAGMA wal_checkpoint 무의미)

**독립 SQLite DB (정상 분리 유지, 이전 대상 아님)**: employment.db, etf_check.db, hs_trade_lab.db,
semiconductor_value_lab.db, sector_followup.db, sector_april.db, reports_catalog.db, collection_health.db, us_market.db

**건전성 우려**:
- system python3 cron(gemini_gems_worker.py)은 `.pth` sitecustomize(venv 전용)를 타지 못해 라우터 우회 → SQLite 직접 기록
- 상위 디렉터리 `/Volumes/Realtek_NVME/stock_dashboard/`(구형 브랜치)의 스크립트들이 라우터 없이 stock.db 직접 접근
- `_is_primary_sqlite_path`의 `uri` 파라미터 미사용 + `file:` 무조건 제거
- WAL/VACUUM 정비 잡에 IS_POSTGRES 가드 없음

### 감사 2 — SQL 변환 완전성
**활성 경로(라이브) 버그 2건**:
1. **`routes/trend.py:520`** — `date('now', 'start of month', 'localtime')` (3-인자 SQLite date()). 변환 규칙 없음 → Postgres `function date(unknown,unknown,unknown) does not exist`. V18 전략 월간 진입 규칙이 `/api/trend` 호출마다 실패. **CRITICAL**
2. **`backtest_common.py:155/161`** — `CREATE TRIGGER ... SELECT RAISE(ABORT,...)`. `executescript()`가 CREATE TRIGGER를 조용히 스킵 → '완료 백테스트는 불변 run spec 필요' 무결성 가드가 PG에서 조용히 소실. (500은 아니지만 의미론적 손실)

**비활성(일회성 스크립트) — scripts/research_*, scripts/ops/***: `is_annual IS TRUE/FALSE`(변환기는 =0/1만 처리),
SQLite `:name` named param, `json_extract()`/`json_object()`, `last_insert_rowid()`.

### 수정 계획 (우선순위)
| 우선 | 작업 | 파일 | 위험 |
|------|------|------|------|
| P1 | `date('now','start of month','localtime')` → `date_trunc('month', now())::date` 변환 규칙 추가 | db_compat.py | 낮음(가산적) |
| P1 | `is_annual IS TRUE/FALSE` 변환 규칙 추가 | db_compat.py | 낮음 |
| P2 | 상대 `DB_PATH="stock.db"` → `__file__` 기준 절대경로(또는 connect_primary_db) | collectors/routes/scheduler ~21파일 | 낮음(기계적) |
| P2 | WAL/VACUUM 잡 IS_POSTGRES 가드 | scheduler.py | 낮음 |
| P3 | CREATE TRIGGER RAISE 변환 또는 CHECK 제약 전환 | backtest_common.py | 중간(의미론) |
| P3 | `_is_primary_sqlite_path` uri 파라미터 수정 | db_compat.py | 낮음 |
| P4 | 비활성 스크립트 SQL 패턴(json_extract/:name/last_insert_rowid) | scripts/* | 낮음(비활성) |

---

## 실제 코드 변경 (2026-09-19) — 후속 검증 필요

### P1 — SQL 변환 규칙 추가 (db_compat.py, 라이브 500 수정)
- `date('now','start of month','localtime')` → `TO_CHAR(date_trunc('month', now()), 'YYYY-MM-DD')`
  → routes/trend.py:520 (V18 월간 진입 규칙)의 라이브 500 해결.
  **주의**: `::date`가 아니라 `TO_CHAR(...)`(TEXT) 사용 — `peak_holding.entry_date`가 TEXT 컬럼임을
  DB introspection으로 확인함(`entry_date -> TEXT`). 기존 date() 변환 규칙도 전부 TO_CHAR(TEXT) 관례.
- `is_annual IS [NOT] TRUE/FALSE` 및 `json_extract()` 초기 변환을 추가했으나, NULL 의미와 JSONPath 의미론은 후속 리뷰에서 결함이 확인되어 2026-09-20에 수정 대상이 되었다.

### P2 — cwd 의존 상대경로 제거 (발산 버그)
`DB_PATH = "stock.db"`(상대) → `str(Path(__file__).resolve().parent.parent / "stock.db")`(절대, `Path` import 필요 시 추가):
- collectors/ 15개: fred, eia_oil_supply, world_bank, oecd_cli, yahoo_macro, imf_weo, reb_housing,
  fao_food_price, ecos, kosis, global_macro_event, global_macro_event_reaction,
  global_financial_conditions, market_quant_bridge, dram_spot (각 `_collector.py`)
- routes/: global_macro.py (`DB_PATH`), sector_rotation.py (`DB`), extra_signals.py (`MAIN_DB`)
- `collectors/us_biotech_pipeline_collector.py`: 독립 SQLite 경로 설정을 추가했으나 실제 연결은 primary PostgreSQL을 사용하므로, 설정/설명 정합화가 필요하다.
- `scheduler.py`: `collect_consensus(db_path="stock.db", ...)` → `collect_consensus(...)` (기본값 사용)
- `scheduler.py:_job_wal_daily_check`: `IS_POSTGRES` 시 stock.db를 WAL checkpoint 대상에서 제외(자체 WAL 관리).

### P3 — 건전성 보강
- `db_compat.py:_is_primary_sqlite_path`: `uri` 파라미터를 실제로 사용(uri=True일 때만 `file:` prefix 제거).
- `backtest_common.py`: `init_backtest_db()`가 Postgres에서 조용히 스킵하던 `RAISE(ABORT)` 트리거를
  plpgsql 함수+트리거로 재현(`_ensure_backtest_pg_triggers`, 모듈 플래그로 1회 실행, `conn.commit()` 포함).

### P4 — 비활성 스크립트
- 초기 자동 변환의 완전성은 검증되지 않았으며, 활성 경로의 named parameter와 batch SQL도 별도 확인이 필요하다.

### 검증 결과 (실행)
- 수정 21개 파일 `py_compile` 통과.
- `translate_sqlite_sql()` 출력 검증: date→TO_CHAR, is_annual IS TRUE/FALSE, json_extract/json_object, `?`→`%s` 모두 정상.
- `_ensure_backtest_pg_triggers()` 실행 → `pg_trigger`에 2개 트리거 생성 확인,
  INSERT/UPDATE 가드가 원본 메시지 그대로 동작함(롤백 트랜잭션으로 무해 테스트).

---

## 재검토 (2026-09-19) — 완료 사인오프 거부, 실재 결함 발견

### 리뷰어 지적 + 실측으로 확정된 결함
1. **DART `INSERT OR REPLACE` 파손 (라이브 데이터 오염)** — `dart_insider_holdings`의 자연키
   UNIQUE `(rcept_no, repror, sp_stock_lmp_cnt, sp_stock_lmp_irds_cnt)`가 PG에서 non-unique로
   강등(`ix_..._nonunique`)됨 → `_execute_insert_or_replace`가 유효 conflict 타깃을 못 찾아
   `ON CONFLICT DO NOTHING`(타깃 없음)으로 폴백 → **REPLACE 대신 항상 INSERT**.
   - SQLite: 78,581행 / 중복 0건(UNIQUE 강제) vs PG: 62,099행 / 중복 그룹 27건(동일 키 10×·5×·3×)
2. **Named-parameter 미지원** — `PostgresCompatCursor.execute()`가 dict 파라미터를 `tuple()`로 깨뜨리고
   `:name` → `%(name)s` 변환이 없었음.
3. **133개 테이블 양방향 행수 드리프트 + 145개 테이블 PG 미존재**(대부분 `_backup_*`/`data_quality_backup_*`지만
   `company_product_mix` 14,482행은 실재 결손).
4. **브리지 `sync_sqlite_bridge_delta.py`** — 자연키 upsert가 `ON CONFLICT(natural_key)`에 UNIQUE 제약을
   요구하므로 강등된 테이블에서 실패. 근본 원인은 `migrate_operational_postgres.create_indexes`가
   `UniqueViolation` 시 UNIQUE 인덱스를 조용히 non-unique로 강등(중복정리 대신)한 것.

### 수정 내역 (본 재검토)
- [완료] `db_compat.py`: `_replace_named_placeholders()`(`:name`→`%(name)s`, `::cast`·문자열 보존) 추가 +
  `PostgresCompatCursor._execute_named()` dict 파라미터 경로 추가. 실쿼리 검증 완료.
- [진행] `dart_insider_holdings`: PG 중복 제거 + UNIQUE 인덱스 재생성 + SQLite 결손분 동기화.

### 잔여 계획 (우선순위)
1. DART 중복 정리 + UNIQUE 인덱스 재생성 + 결손분 동기화.
2. 133 테이블 드리프트 / 145 결손 정합성 재조정 + 브리지 복구.
3. 백테스트 무결성 가드 동등성 확인(plpgsql 트리거가 원본 메시지 그대로 동작 — 검증됨).
4. 사장 헬퍼 재분류(`US_DB_PATH`, `collect_consensus.db_path`는 실제 미사용 확인).
5. PostgreSQL 호환 회귀 테스트 추가.

### 실행 결과 (재검토)
- [완료] `db_compat.py` named-param 지원(`_replace_named_placeholders` + `_execute_named`) — 실쿼리 검증.
- [완료] `dart_insider_holdings`: PG 중복 43행 제거 + UNIQUE 인덱스 `ix_dart_insider_holdings_nk` 재생성.
  → `INSERT OR REPLACE`가 이제 자연키로 정상 REPLACE. (재현·검증 완료)
- [롤백] 브리지(`sync_sqlite_bridge_delta.py`) 즉시 동기화는 **의도치 않게 NULL-키 28,322행을 중복 적재**
  (`ON CONFLICT(natural_key)`는 NULL≠NULL이라 NULL 키를 매칭 못 함)하여 백업 테이블로 원복(62,056행 복원).
- [완료] `tests/test_db_compat_translation.py` 신규 — SQL 변환 회귀 테스트 10건 전부 통과.
- [미완/보류] **브리지 NULL-키 버그 + 133 테이블 드리프트 정합성 재조정** — sync 방향(PG-주 vs SQLite-주)이
  테이블별로 달라 섣불리 실행하면 안 됨. 별도 정밀 재조정 계획 필요.

---

## 변경 이력
| 날짜 | 파일 | 변경 내용 |
|------|------|-----------|
| 2026-09-19 | (없음 — 조사만 수행) | 마이그레이션 현황 조사 + 이 문서 작성. 코드 변경 없음. |
| 2026-09-19 | hermes_change.md | 교차검증 결과(라우터 갭 21파일 + SQL 변환 갭 2건 라이브) 기록. 코드 변경 없음. |
| 2026-09-19 | db_compat.py | date('start of month')/is_annual IS TRUE·FALSE/json_extract/json_object 변환 규칙 추가 + `_is_primary_sqlite_path` uri 파라미터 적용 |
| 2026-09-19 | collectors/(15) + routes/(3) | 상대 `DB_PATH="stock.db"` → `__file__` 기준 절대경로 (cwd 의존 발산 제거) |
| 2026-09-19 | scheduler.py | WAL 잡 IS_POSTGRES 가드 + collect_consensus 호출 인자 정리 |
| 2026-09-19 | backtest_common.py | PostgreSQL 무결성 트리거(plpgsql) 재현 + conn.commit() — RAISE(ABORT) 가드 복원 |
| 2026-09-19 | collectors/us_biotech_pipeline_collector.py | US_DB_PATH 기본값을 절대 US 마켓 DB 경로로 변경 |
| 2026-09-19 | db_compat.py | SQLite `:name` named-parameter 지원(`_replace_named_placeholders` + `_execute_named`) — dict 파라미터 파손 수정 |
| 2026-09-19 | dart_insider_holdings (데이터) | PG 중복 43행 제거 + 자연키 UNIQUE 인덱스 재생성 — `INSERT OR REPLACE` 파손 복구 (브리지 즉시동기화는 NULL-키 중복으로 롤백) |
| 2026-09-19 | tests/test_db_compat_translation.py | SQL 변환 회귀 테스트 10건 신규 (date/is_annual/json/named-param/qmark) |
| 2026-09-20 | scripts/backfill_quarterly_bs_v2.py | QUARTERLY_4WAY source_count=0 잔여 balance-sheet(total_assets/total_equity) 대상 DART 보고서 XML 필드복원 파이프라인 신규(감사/멱등/체크포인트) + 194건 복원 |

---

## 2026-09-20 — QUARTERLY_4WAY 잔여 출처 0건 balance-sheet 필드복원 파이프라인 (code-doer)

### 작업 내용
- `scripts/backfill_quarterly_bs_v2.py` 신규: QUARTERLY_4WAY `status='OPEN' AND source_count=0`
  잔여 4,824건 중 **point-in-time인 balance-sheet(total_assets/total_equity) 4,129건**을
  DART 분기(Q1)·반기(Q2)·분기(Q3)·사업(Q4)보고서 원문 XML(`document.xml`)의
  **ConsolidatedMember(연결) + eFY IFRS fact**에서 필드 단위 복원하는 통합 파이프라인.
- 파서는 기존 검증된 `collectors/dart_document_financials.extract_core_fact_details`와
  3개 표본 rcept_no에서 값·consolidated 플래그 완전일치(head-to-head 검증)를 확인.
- 감사 로그: `financial_fix_log`(fixed_at/row_id/stock_code/year/quarter/field_name/
  old_value=NULL/new_value/source=XML URL+acode+unit) + `fin_quarterly_validation_flags`
  (dart_value/source_count 0→1/notes). `old_value` 전수 NULL 확인.
- 안전장치: ①`WHERE <field> IS NULL` ②`source_count=0 AND status='OPEN'` ③확정값
  (source_count≥2) 구조적 미접근 ④JSON checkpoint+`--resume` ⑤DART `020` 감지 중단.

### 실행 결과
- **194 필드 복원**(금일 세션, 이전 469건과 합산 663건). `financial_fix_log` 194행,
  중복 0, cross-check 불일치 0, `source_count>=2` 접근 0.
- 잔여 `source_count=0`: balance-sheet 3,935 / 전체 4,630 (income-statement 695건은
  Q2~Q4 누적(YTD)값이라 차감 없이 직접 기입 불가 — 별도 파이프라인 필요).
- 미복원 주 사유: ①연결재무 미공시(별도 only) ②`dart_disclosures`에 해당 보고서 부재
  ③보고서 XML에 표준 fact 부재. `--resume`으로 일일 document.xml 한도 내 지속 가능.

### 산출물
- 감사 CSV: `research_outputs/bs_backfill_v2_audit_20260920.csv` (194행, 추적 가능)
- 진단/검증: `scratch/diag_4way_state.py`, `scratch/diag_fillability.py`,
  `scratch/validate_parser_head2head.py`, `scratch/final_verify_bs_v2.py`

| 2026-09-20 | docs/codex_handoff_is_quarterly_standalone_design_20260920.md, scratch/is_standalone_reference.py | 손익계산서 당분기(standalone) 복원 설계 제출(값 쓰기 없음): 당분기 fact 직접추출 1순위 + 누적차감 폴백, dart_OperatingIncomeLoss 매핑 버그 발견, ACONTEXT 기간토큰(FQ/HY/TQ/FY)·Q/A suffix·연결/별도·총액vs세부 차단규칙 + self-test 7/7 |
| 2026-09-20 | docs/codex_handoff_is_standalone_impl_spec_20260920.md, tests/test_is_standalone_pipeline.py | 손익계산서 당분기 파이프라인 TDD 명세 + failing test 14건 작성(값 쓰기 없음). RED 확인: scripts/is_standalone_pipeline 미구현 |


## 2026-09-20 22:39:46 — P0 price_history 무결성 가드 (code-doer)

### 작업 내용
1. **close<=0 fail-closed 거부**: `price_integrity.py`의 트리거 함수 `guard_historical_price_write()`에
   `IF NEW.close IS NULL OR NEW.close <= 0 THEN RAISE EXCEPTION ...`을 최상단(검증 플래그
   `app.price_basis_checked` 우회보다 먼저)에 추가. 함수 본문은 `WRITE_GUARD_FUNCTION_SQL` 상수로
   분리(테스트 재사용). 라이브 적용 경로: `scripts/apply_price_write_guard.py`(DROP/CREATE TRIGGER).
2. **수급 6필드 보존 upsert**: `crud.py` `merge_supply_fields(new_row, existing)` 신규 — 장중 1분
   가격 갱신이 수급=0으로 들어와도 기존 inst/frn/ind + 금액3 필드를 보존. 기존엔 inst/frn 2필드만 보존.
3. **비KR종목 공통필터**: `security_master.py` `is_kr_equity_code()`(6자리 [0-9A-Z]만 허용) →
   `routes/buy_candidates.py`의 `add_buy_candidate`(POST, 400 거부)와 자동보드 `add_candidate`(스킵).

### 테스트 (TDD)
- `tests/test_price_history_guard.py` 6건: `is_kr_equity_code` 2, `merge_supply_fields` 3,
  트리거 close<=0 롤백온리 검증 1(임시테이블에 동일 함수/트리거 적용, ^TESTX·검증플래그 경로 모두 거부 확인).
- RED 확인 후 GREEN. 기존 `test_price_integrity`/`test_security_master_code_filter`/
  `test_db_compat_regressions`/`test_db_compat_translation`/`test_selected_strategy_price_integrity`
  합산 46건 전부 통과(무손상).

### 파일
- 수정: `price_integrity.py`, `crud.py`, `security_master.py`, `routes/buy_candidates.py`, `CLAUDE.md`
- 신규: `tests/test_price_history_guard.py`
- DB migration: 코드 DDL만 변경(트리거 함수). 라이브 반영은 `scripts/apply_price_write_guard.py` 재실행 = human 승인 필요.

### 운영 노트
- 라이브 DB = PostgreSQL(127.0.0.1:5432/stock_dashboard). `close<=0` 행 0건, `UNIQUE(stock_code,date)`
  인덱스(`ix_price_history_0b57a1f65a`) 존재, 중복 0건. 비KR코드(^KS11/GC=F/USDKRW=X 등) 55,178행은
  매크로·지수 정상 데이터로 price_history에 유지(스크리너에서만 제외).
- price_history에 AccessShareLock을 잡은 idle-in-transaction 세션 10+개 → `apply_price_write_guard.py`
  의 DROP/CREATE TRIGGER가 락 대기로 statement_timeout(30s)에 걸릴 수 있음. 쓰기 트래픽 없는 시간대에 적용.

## 2026-09-20 code-doer
- [검증] cafe_stock_indicator_mappings: PG+38은 신규 macro 매핑, 중복0, SQLite⊆PG 100% (기존 +213은 스테일)
- [신규] scripts/checksum_parity_report.py — SQLite⊆PG 값-해시 포함검증 (핵심테이블 유실0, drift=live-primary 기대치)
- [신규] tests/test_intraday_concurrency.py — 장중수집 쓰기×동시조회 동시성 PASS
- [검증] PG장애 자동롤백 미구현 확인 (수동 복구만)

## 2026-09-20 code-doer — 손익계산서 당분기 파이프라인 GREEN
- [구현] `scripts/is_standalone_pipeline.py` — `derive_standalone`(순수함수) + `record_standalone`(3계층 분리·멱등)
- [검증] `tests/test_is_standalone_pipeline.py` **13/13 GREEN** (RED→GREEN). PostgreSQL 전용(raw sqlite3.connect 0건),
  무추정(SKIP: `no_fact`/`missing_prior_cumulative`/`negative_revenue`), 멱등(`{field} IS NULL` 가드 + provenance dedup)
- [정적검사] UPDATE/INSERT/GUARD 3개 SQL `translate_sqlite_sql` 변환 확인(`?`→`%s`, `is_annual IS FALSE`→`::text IN`). commit 미발행(rollback-safe)
- [미변경] 라이브 PG 쓰기 0건(테스트 `:memory:` + 문자열 변환만). DDL 없음(테이블은 migration/REFACTOR 단계에서 생성)

## 2026-09-20 code-doer — 손익계산서 당분기 파이프라인 REFACTOR 완결 (P0 승인보류 항목 해소)

### 작업 내용
1. **migration (신규)** `scripts/migrate_is_standalone_derivation.py`: `is_standalone_derivation`
   테이블 DDL(PG 전용). `--check`(읽기전용 기본) / `--apply` / `--rollback` 모드. 타입은
   `DOUBLE PRECISION`(PG float4 정밀도 손실 방지) + `id BIGINT ... IDENTITY`. raw SQLite 접근 0건.
2. **financial_fix_log 연동**: `record_standalone`이 NULL→값 실제 기입 시에만 `financial_fix_log`
   1행 기록(`fix_rule='DART_STANDALONE_IS_DERIVATION'`, `old_value=NULL`, `row_id=financial_data.id`).
3. **source_count 0→1 승격**: `fin_quarterly_validation_flags`(`QUARTERLY_4WAY`+`OPEN`+`source_count=0`)
   → `dart_value/source_count=1/notes/updated_at` 갱신. `WHERE source_count=0` 가드로 멱등.

### 미확정 항목 실측 확정
- **flag.field = `operating_profit`**(≠ `op_profit`) — live distinct field 5종 확인.
- **Q4 `FY` 토큰 실측**: 삼성전자 005930 사업보고서(`rcept_no=20260310002820`) 원문. 토큰=`FY` 확정,
  그러나 Q4 손익 fact는 Q/A suffix가 없고(`CFY2025dFY_` 연간누적, 비교 `BPFY`) → `derive_standalone`은
  Q4를 안전하게 SKIP(오파생 없음). Q4 standalone 복원은 "FY연간−Q3누적" 별도 설계 필요(P0 범위 외).

### 테스트 (TDD)
- `tests/test_is_standalone_pipeline.py` **15/15 GREEN** (fix_log/flag/멱등/기확정값 비침범 4건 추가).
- 관련 회귀 합산 **62 passed**: `test_db_compat_regressions`(4) + `test_db_compat_translation`(10) +
  `test_price_integrity`(27) + `test_price_history_guard`(6).

### 검증 산출물
- `scratch/verify_p0_integration.py`: 정적검사(0건) + SQL 변환 5문 + migration rollback
  (`CREATE→확인→ROLLBACK→부재`, 상태 무잔류). `scratch/measure_q4_fy_token.py`·`dump_q4_contexts.py`
  (Q4 토큰 실측). 라이브 PG 쓰기 0건, `--apply`는 human 승인 대기.

### 파일
- 수정: `scripts/is_standalone_pipeline.py`, `tests/test_is_standalone_pipeline.py`, `hermes.md`
- 신규: `scripts/migrate_is_standalone_derivation.py`

## 2026-09-20 code-doer — P1 live fail-closed read-gate 명세 + RED 제시

### 작업 내용 (값 쓰기 없음)
- P0 GO 승인 후 P1 착수. Stage-1 근거를 라이브 PG로 재확인(read-only):
  view `canonical_price_history_v` 등 존재, `canonical_financial_data` 93,734행,
  `price_integrity_quarantine` 1,855,059행 — 게이트 레이어 충족.
- raw 직접조회 현황: `signal_engine.py` price_history 48 + financial_data 17,
  `screener.py` price_history 5 + financial_data 4.

### 산출물
- `docs/codex_handoff_p1_read_gate_spec_20260920.md`: 공용 helper `signal_data_gate.py`
  계약(`read_prices_failclosed`=canonical return_usable=1만 + excluded reason→count,
  `read_financials_failclosed`=canonical_financial_data만), fail-closed·무사일ent-exclusion·
  point-in-time(가용일) 규칙.
- `tests/test_signal_data_gate.py`: G1(격리가격 행 제외+사유 보고)·G2(BS-identity 위반 raw
  미노출) assert. **RED 확인**: `ModuleNotFoundError: signal_data_gate`.

### 다음 단계
- GREEN: `signal_data_gate.py` 구현 → `signal_engine.py`/`screener.py` read 경로 swap +
  `excluded` 로깅. `tenbagger_engine.py`·Minervini allowlist는 별도.

### GREEN 완료 (동일 세션)
- `signal_data_gate.py` 구현(READ-ONLY, 필드 allowlist로 주입 차단):
  `read_prices_failclosed`(canonical `return_usable=1`만 + excluded reason→count),
  `read_financials_failclosed`(`canonical_financial_data`만 + `as_of` 가용일 필터).
- `tests/test_signal_data_gate.py` 3/3 GREEN(RED→GREEN). 관련 회귀 합산 59 passed.
- 라이브 PG read-only 검증: 005930 정상(usable=14/excluded={}), 격리종목 001000
  2026-09-11 → usable=0, excluded={raw_source_confirmed_jump_review:1} fail-closed 확인.
- 다음 단계: `signal_engine.py`/`screener.py` read 경로 swap + excluded 로깅.

## [2026-09-20 code-doer] F01~F09 수정본 ↔ 런타임 경로 독립 확인
- 결론: F01~F09 9개 항목 모두 런타임 코드에 반영 확인. 회귀테스트 34/34 passed.
- 파일별: sector.py(F01/F07/F08), merged_simulator.py(F02/F03/F04/F09),
  portfolio_engine.py(F03/F04), routes/trend.py(F05/F06).
- 주의사항: ① 4개 파일 미커밋(716+/109-, working-tree `M`) → clone 시 유실 위험.
  ② 규격문서 docs/claude_handoff_strategy_code_findings_20260912.md는 runtime/docs/에
  없고 루트 레포(automate-stock-discovery-44sMp)에만 존재 → 코드↔규격 colocation 불일치.

## [2026-09-20 code-doer] 커밋 후보 분리 + 정본 복제 + 테스트 수량 정정
- 테스트 정정: 지정 7파일 = 40 passed (이전 34는 partial_sell 누락, checker 39도 1건 불일치).
- 정본: findings/resume 문서 root→runtime/docs 복제(sha256 일치, chmod 444),
  manifest docs/SPEC_SHA256_F01F09_20260920.txt.
- 커밋 후보: 14파일 staged(4 src+7 test+2 doc+1 manifest), 1731+/109-,
  572 작업트리 파일은 unstaged 유지, patch .verification/f01f09_commit_candidate_20260920.patch.
- scope 주의: sector.py(cost_multiplier/pnl_krw 13곳, resume-P1), trend.py(orphaned 6곳,
  resume-P3)가 F01~F09와 혼재.

## [2026-09-21 code-doer] FDR/pykrx 보조소스 원천대조 + 국내주가 결측/오염 진단
- 신규: `scripts/backfill_price_fdr.py` (DRY-RUN 기본, --apply 결측 복구).
  권위원천 결정: Naver 일봉 = pykrx get_market_ohlcv = FDR DataReader (상호 100% 일치).
  FDR StockListing의 Close 필드는 신뢰불가(삼성전자 9/18 261,000 vs 정산 260,000) → universe용만.
- 크로스밸리데이션(라이브 PG, 9/18, 샘플40):
  universe=2681, db_present=347, missing=2334,
  db_corruption=25/40(62.5%! 기존 보유값이 정산종가와 0.1~4% 불일치),
  adjustment_basis_mismatch=0 (FDR수정 vs pykrx원 최근창 일치 → 조정기준 이슈 없음).
- 결측 복구 계획: 9/14~9/18 총 11,653건 (일별 ~2,324).
- 루트원인: 9/12경 야간 전종목 KRX/KIS 확정수집이 끊겨 최근 행이 장중스냅샷(created_at NULL,
  종가/거래량이 정산치와 불일치)으로 남음. 005930 예: DB 9/18=261,000(시가) vs 정산 260,000.
- pykrx 1.2.4 API 개명: get_market_net_purchases_of_business_day → get_market_net_purchases_of_equities.
  호출부 2곳(krx_collector.py:341, kis_client.py:406) 구 API 호출 → AttributeError(무음 소거).
  단, 신 API도 EMPTY 반환(KRX 투자자 엔드포인트 차단) → FDR/pykrx는 수급 복구 불가.
- 투자자수급(kiwoom_investor_daily)·섹터지수(sector_index_daily) 9/11 정지, 거시브리지 11일 stale
  은 FDR/pykrx와 무관한 별개 파이프라인 장애.

