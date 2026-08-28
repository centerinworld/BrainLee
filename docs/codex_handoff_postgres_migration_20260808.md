# Codex Handoff: PostgreSQL Migration Bootstrap

Timestamp: 2026-08-08 22:40 KST

## Operational Cutover Update (2026-08-10 08:13 KST)

PostgreSQL is now the operational primary and the schema/data cutover is complete.
This supersedes the older bootstrap counts and staging notes below.

### Confirmed state

- `public` contains all 219 active operational tables from SQLite.
- Full-table validation reports zero tables where PostgreSQL is behind SQLite.
- Core counts: `price_history=8,183,652`, `financial_data=191,947`,
  `cash_flow_data=138,433`, `backtest_runs=97,158`,
  `backtest_run_specs=95,060`, `tenbagger_results=2,510`, and
  `quant_major_indicator_series=185,493`.
- `price_history` spans `2010-01-04` through `2026-08-09` and 4,274 codes.
- PostgreSQL has 507 indexes in `public`.
- Only the `public` application schema remains. The internal pre-cutover and test
  schemas were removed after the external dump was validated.
- Database size fell from about 32.3 GB to 24.0 GB after cleanup.
- Live automatic trading remains disabled (`STOCKEASY_LIVE_AUTOTRADE=false`).
- launchd serves the backend on `127.0.0.1:8000` and frontend on port `5173`.
- Live HTTP checks passed for dashboard stats, tenbagger results, cherry screener,
  trend holdings, market indicators, company intelligence, backtest list/matrix,
  and the selected-run registry.

### Runtime paths converted in this cutover

- SQLAlchemy application primary and dashboard stats.
- Tenbagger reads, calculations, and result writes.
- Backtest engine run/spec writes and backtest API reads/writes.
- Backtest verification registry and artifacts.
- Quant-major-indicator cron and its primary source reads/writes.
- All callers using `db_utils.connect_stock_db()` now route to PostgreSQL.
- SQLite qmark/date/boolean/PRAGMA/`INSERT OR IGNORE` compatibility was expanded,
  including SQLite-style numeric result normalization.

### Verification commands

```bash
cd /Applications/stock_dashboard
venv/bin/python scripts/verify_postgres_cutover.py
venv/bin/python scripts/verify_tenbagger_postgres.py
```

Both commands must end with `"ok": true` and no failures. The full report is
written to `research_outputs/postgres_cutover/verification_latest.json`.

### Rollback artifact

The validated pre-cutover PostgreSQL custom-format dump is:

```text
/Volumes/Realtek_NVME/stock_dashboard/postgres_public_pre_cutover_20260810.dump
```

- Size: 843,153,456 bytes.
- `pg_restore -l` successfully read 303 TOC entries.
- Do not delete this dump until the legacy SQLite retirement work is complete.

### Important remaining boundary

The operational data cutover is complete, but repository-wide SQLite retirement
is not. The FastAPI process still opens `stock.db` because a number of older
routes, collectors, schedulers, and research scripts use direct
`sqlite3.connect(...)` calls instead of the shared connection helper. Independent
stores such as `hs_trade_lab.db`, `employment.db`, and ETF session databases are
also intentionally separate and are not part of the PostgreSQL primary.

Do not make `stock.db` read-only or delete it yet. First convert each direct caller,
run its endpoint/collector test, and confirm with `lsof` that the backend no longer
holds the file. `scripts/sync_sqlite_bridge_delta.py` is an idempotent emergency
bridge for keyed legacy tables; it is not the desired permanent architecture.

Known source-data constraint exceptions preserved during cutover:

- `dart_employee_count`: nullable legacy composite primary key.
- `consensus_targets`: duplicate legacy natural keys; non-unique lookup index.
- `dart_insider_holdings`: duplicate legacy natural keys; non-unique lookup index.

## Final Status Update (23:35 KST)

The tenbagger production path is now running on PostgreSQL end to end.

- `routes/tenbagger.py` stock-data connections use the configured primary DB.
- `tenbagger_engine.py` reads signals and writes discovery results to PostgreSQL.
- 32 required tenbagger tables were migrated and row-count verified.
- SQLite indexes, including natural-key unique indexes, were recreated in PostgreSQL.
- A SQLite-SQL compatibility layer handles qmark parameters, date functions, boolean predicates, row access, and failed-query transaction recovery.
- A 30-minute incremental upsert synchronizer keeps PostgreSQL current while remaining legacy collectors are retired.
- A full discovery run analyzed 2,470 candidates in 88.38 seconds and saved 20 results to PostgreSQL.
- Live HTTP checks passed for results, screener v2, stock insight, and the empirical scoreboard.
- The empirical scoreboard processed 109,311 rows in 1.46 seconds and returned 11 filter comparisons.
- The frontend production build passed.

One-command verification for Claude:

```bash
cd /Applications/stock_dashboard
venv/bin/python scripts/verify_tenbagger_postgres.py
```

Expected final fields:

```json
{"ok": true, "failures": []}
```

## User Intent

The user asked whether SQLite can be replaced and then instructed: "변경해".

The practical goal is to move the project away from the single large SQLite `stock.db`, because repeated DB issues were caused by:

- one large SQLite file shared by server, scheduler, collectors, backfills, and ad hoc scripts,
- many raw `sqlite3.connect(...)` call sites,
- `stock.db` being a symlink to `/Volumes/Realtek_NVME/stock_dashboard/stock.db`,
- inconsistent timeout/WAL/read-only behavior.

## What Changed

### Postgres Runtime

Added local PostgreSQL 16 compose file:

- `/Applications/stock_dashboard/docker-compose.postgres.yml`

Service:

- container: `stock_dashboard_postgres`
- database: `stock_dashboard`
- user: `stock_dashboard`
- password: `stock_dashboard_local`
- port: `5432`

The container was started and verified healthy with:

```bash
docker compose -f docker-compose.postgres.yml ps
```

Observed result:

```text
stock_dashboard_postgres ... Up ... (healthy) ... 0.0.0.0:5432->5432/tcp
```

### Python Dependency

Updated:

- `/Applications/stock_dashboard/requirements.txt`

Added:

```text
psycopg[binary]
```

The current venv was also updated successfully:

```bash
venv/bin/pip install 'psycopg[binary]'
```

### App DB Configuration

Updated:

- `/Applications/stock_dashboard/config.py`
- `/Applications/stock_dashboard/database.py`

Behavior now:

- `POSTGRES_DATABASE_URL` is preferred over `DATABASE_URL`.
- SQLite-only SQLAlchemy `connect_args` are used only for SQLite.
- SQLAlchemy engine is created with `pool_pre_ping=True`.

Current `.env` was updated with:

```text
POSTGRES_DATABASE_URL=postgresql+psycopg://stock_dashboard:stock_dashboard_local@127.0.0.1:5432/stock_dashboard
```

Do not print or rewrite `.env` wholesale; it contains live secrets.

### SQLAlchemy Upsert Compatibility

Updated:

- `/Applications/stock_dashboard/crud.py`

The `insert` dialect now switches:

- Postgres: `sqlalchemy.dialects.postgresql.insert`
- SQLite: `sqlalchemy.dialects.sqlite.insert`

The remaining canonical financial gate still uses legacy SQLite through `connect_stock_db()` as an interim compatibility path. This is intentional for now because the canonical gate functions are still SQLite-specific.

### SQLite Path Stabilization

Updated:

- `/Applications/stock_dashboard/db_utils.py`

`STOCK_DB_PATH` now resolves the symlink:

```text
/Volumes/Realtek_NVME/stock_dashboard/stock.db
```

`connect_stock_db()` now applies:

- `busy_timeout`,
- `foreign_keys=ON`,
- optional WAL for legacy SQLite writes.

This remains useful while raw SQLite paths are being retired.

### Migration Scripts

Added:

- `/Applications/stock_dashboard/scripts/check_postgres_ready.py`
- `/Applications/stock_dashboard/scripts/migrate_sqlite_to_postgres.py`
- `/Applications/stock_dashboard/docs/postgres_migration.md`

`migrate_sqlite_to_postgres.py` copies SQLite tables into Postgres while preserving table and column names. It also converts SQLite boolean-ish `0/1` values into PostgreSQL booleans for columns declared as `BOOLEAN`.

## Data Migrated

Command executed:

```bash
venv/bin/python scripts/migrate_sqlite_to_postgres.py --drop \
  --only stock_universe price_history financial_data cash_flow_data dilution_events \
  tenbagger_results tenbagger_ai_analysis tenbagger_daily_alerts \
  --batch-size 10000
```

Observed copied rows:

```text
cash_flow_data: 138,433 rows
dilution_events: 17,931 rows
financial_data: 191,935 rows
price_history: 8,183,583 rows
stock_universe: 2,693 rows
tenbagger_ai_analysis: 8 rows
tenbagger_daily_alerts: 613 rows
tenbagger_results: 2,490 rows
```

Postgres query verification also confirmed:

```text
cash_flow_data 138433
dilution_events 17931
financial_data 191935
price_history 8183583
stock_universe 2693
tenbagger_results 2490
```

## Verification Already Completed

### Compile

```bash
venv/bin/python -m py_compile \
  /Applications/stock_dashboard/config.py \
  /Applications/stock_dashboard/database.py \
  /Applications/stock_dashboard/crud.py \
  /Applications/stock_dashboard/scripts/migrate_sqlite_to_postgres.py \
  /Applications/stock_dashboard/scripts/check_postgres_ready.py
```

Result: passed.

### Postgres Readiness

```bash
venv/bin/python scripts/check_postgres_ready.py
```

Observed:

```text
{'database': 'stock_dashboard', 'user': 'stock_dashboard', 'version': ['PostgreSQL', '16.14']}
```

### SQLAlchemy Dialect

```bash
venv/bin/python - <<'PY'
from config import DATABASE_URL, IS_POSTGRES, IS_SQLITE
from database import engine
print({'driver': DATABASE_URL.split(':', 1)[0], 'is_postgres': IS_POSTGRES, 'is_sqlite': IS_SQLITE, 'dialect': engine.dialect.name})
PY
```

Observed:

```text
{'driver': 'postgresql+psycopg', 'is_postgres': True, 'is_sqlite': False, 'dialect': 'postgresql'}
```

### FastAPI Import

```bash
venv/bin/python - <<'PY'
import main
from database import engine
from sqlalchemy import text
with engine.connect() as conn:
    rows = conn.execute(text('SELECT COUNT(*) FROM price_history')).scalar()
print({'app_loaded': True, 'dialect': engine.dialect.name, 'price_history': rows})
PY
```

Observed:

```text
{'app_loaded': True, 'dialect': 'postgresql', 'price_history': 8183583}
```

## Scope Boundary

The tenbagger production path is complete on PostgreSQL. The entire multi-feature repository is not yet SQLite-free.

During the remaining bridge phase:

- PostgreSQL is the app and tenbagger primary DB.
- Some non-tenbagger collectors and research scripts still write legacy SQLite.
- `scripts/sync_tenbagger_postgres.py` upserts the 30 tenbagger source tables every 30 minutes, including `dart_backlog_quarterly` for confidence-gated backlog signals.
- `scripts/verify_tenbagger_postgres.py` fails if a required source table falls behind.
- Separate databases such as `employment_monitor/employment.db` and `hs_trade_lab.db` intentionally remain SQLite because they are independent stores.

Known raw SQLite patterns still requiring follow-up include:

- `PRAGMA ...`
- `INSERT OR REPLACE`
- `date('now', ...)`
- `sqlite_master`
- direct `sqlite3.connect("stock.db")` or `sqlite3.connect(DB_PATH)`

## Recommended Claude Verification Checklist

1. Run the complete automated verification:

```bash
venv/bin/python scripts/verify_tenbagger_postgres.py
```

2. Confirm container health:

```bash
docker compose -f docker-compose.postgres.yml ps
```

3. Confirm app uses Postgres:

```bash
venv/bin/python - <<'PY'
from database import engine
print(engine.dialect.name)
PY
```

Expected:

```text
postgresql
```

4. Confirm live APIs:

```bash
curl -sS 'http://127.0.0.1:8000/api/tenbagger/results?limit=3'
curl -sS 'http://127.0.0.1:8000/api/tenbagger/screener-v2?page_size=3'
curl -sS 'http://127.0.0.1:8000/api/tenbagger/empirical-scoreboard?min_score=55&limit=250000'
```

5. Confirm incremental synchronization is idempotent:

```bash
venv/bin/python scripts/sync_tenbagger_postgres.py
venv/bin/python scripts/verify_tenbagger_postgres.py
```

6. Confirm frontend build:

```bash
npm --prefix frontend run build
```

7. Inspect remaining non-tenbagger raw SQLite call sites:

```bash
rg -n "sqlite3\.connect\(|INSERT OR REPLACE|PRAGMA|date\('now'|sqlite_master" \
  /Applications/stock_dashboard/main.py \
  /Applications/stock_dashboard/routes \
  /Applications/stock_dashboard/scripts \
  /Applications/stock_dashboard/collectors
```

## Recommended Next Work

1. Convert remaining non-tenbagger collector write paths to PostgreSQL, one collector family at a time.
2. Convert non-tenbagger API routes still using raw SQLite.
3. Move the canonical financial write-gate logic off SQLite.
4. Remove the incremental bridge only after `rg` confirms no relevant legacy writers remain.
5. Freeze, archive, and only then remove the legacy `stock.db`.

Do not delete SQLite yet. It is still needed by legacy paths.

## Historical Tenbagger Logic V2 (2026-08-08 PM)

The historical research path now separates a transient 10x price print from a
business-validated tenbagger. It is deliberately not wired to current-stock
recommendations or automated trading.

### Target and leakage controls

- Raw target: monthly close reaches 10x within 24 months.
- Validated target: raw target plus at least one annual report within the next
  24 months showing revenue growth of 15% or more, positive operating profit,
  and either 20% operating-profit growth or a turnaround.
- Persistence requirement: monthly close remains at least 3x for three months.
- Input earnings are restricted to conservative report availability dates.
- Annual/future earnings are used only to classify the historical outcome, not
  as candidate input features.
- Rule selection uses 2020-2022 only. The 2023-2024 mature-label period is read
  only after train selection.
- `model_score_12m` is excluded because its 2024-06 training cutoff overlaps the
  validation period.

### Reproducible outputs

```bash
venv/bin/python scripts/research_historical_tenbagger_scoreboard_v2.py
curl -sS http://127.0.0.1:8000/api/tenbagger/historical-scoreboard-v2
```

Artifact:

```text
research_outputs/historical_tenbagger_scoreboard_v2.json
```

The integrated scheduler rebuilds this artifact every Monday at 08:10 KST.
Writes use a temporary file followed by an atomic replace, so the API never
serves a partially written JSON document.

Latest measured dataset:

- 126,879 mature monthly rows across 2,512 stocks.
- 1,909 raw 10x rows.
- 426 business-validated persistent 10x rows.
- 1,483 rows separated as issue/transient proxies.
- No duplicate `(snapshot_date, stock_code)` keys.
- Point-in-time earnings coverage: 91.9%.

The leading precision-core rule selected on train retained 5.78x validation
lift, with 2.19% validated precision and 4.65% distinct-winner recall. The
leading coverage-watchlist rule retained 1.55x validation lift and 13.95%
distinct-winner recall. These are separate tiers because no single rule had
both high precision and broad recall.

`동양고속` and `천일고속` were explicit audit cases. Their 9 and 2 raw 10x rows,
respectively, all fail the strengthened business-validation label. This is not
implemented as a name blacklist.

### Files changed for V2

- `scripts/research_historical_tenbagger_scoreboard_v2.py`
- `research_outputs/historical_tenbagger_scoreboard_v2.json`
- `routes/tenbagger.py`
- `frontend/src/views/TenbaggerProjectView.jsx`

### Residual limitations

- `strategy_feature_snapshot` is based on the current security universe, so
  delisted-stock survivorship bias remains possible.
- Validation winner counts are still small; the reported lift is evidence, not
  proof of a perfect or guaranteed strategy.
- Broad coverage rules are materially weaker than precision rules. Do not merge
  the two tiers into one score without a new train/validation experiment.

## Historical Cause Audit Correction (2026-08-09)

The 2026-08-08 counts above are superseded. A daily-price audit found mixed
adjusted/raw or corporate-action discontinuities that had created false 10x
labels. The V2 builder now rejects every outcome window containing a daily
close ratio above 1.45 or below 0.69, including the 35 days before its base
snapshot. This is a universal data rule, not a stock-name blacklist.

Latest cleaned result:

- 114,771 eligible monthly rows across 2,489 stocks.
- 793 raw 10x rows on usable price series.
- 278 persistent, annual-business-backed 10x rows.
- 515 valid-price but non-persistent/non-business 10x rows.
- 12,108 monthly rows excluded for price-series artifacts.
- 1,116 previously counted 10x rows excluded as price artifacts.
- Seven precision finalists and one coverage finalist pass the unchanged
  out-of-sample stability gate; the API exposes up to five from each tier.

The separate cause audit examines the earliest clean validated episode per
stock. It splits evidence into ignition (through 30 days after the first 50%
rise) and scaling (through 30 days after the first 3x date). Only earnings,
contracts, and structural business changes are eligible for the reusable
business training cohort. Financing, shareholder return, and unresolved cases
are not included in that cohort.

Latest cause result:

- 60 distinct clean persistent winners reviewed.
- 51 have a source-timed cause classification.
- 48 are business-cause training eligible.
- 3 are excluded as non-operating causes.
- 9 require manual source review: HMM, PSK Holdings, Seoul Auction, Selvas AI,
  Wemade, Cosmecca Korea, HD Hyundai Energy Solutions, Doosan Fuel Cell, and
  SAMG Entertainment.
- Shinsung Delta Tech is explicitly classified as an LK-99 theme event and
  excluded from the business cohort, even if later financial data improve.

Reproduce and verify in this order:

```bash
venv/bin/python scripts/research_historical_tenbagger_scoreboard_v2.py
venv/bin/python scripts/research_historical_tenbagger_causes.py
curl -sS http://127.0.0.1:8000/api/tenbagger/historical-scoreboard-v2
curl -sS http://127.0.0.1:8000/api/tenbagger/historical-causes
npm --prefix frontend run build
```

Artifacts:

```text
research_outputs/historical_tenbagger_scoreboard_v2.json
research_outputs/historical_tenbagger_causes.json
research_outputs/historical_tenbagger_causes.csv
research_outputs/historical_tenbagger_causes.md
```

The Monday 08:10 scheduler now rebuilds both the scoreboard and the cause audit
sequentially. A scoreboard success followed by a cause-audit failure is logged
as a historical-validation failure and must be investigated.

## Historical Leading-Signal Discovery (2026-08-09)

`scripts/discover_historical_tenbagger_signals.py` searches only point-in-time
snapshot inputs. It excludes future model scores and future financial results.
The target contains 57 clean persistent winners; the three theme, financing,
or shareholder-return cases are excluded, while unresolved operating cases are
retained to avoid circularly favoring contract-derived cause labels.

Time split:

- Discovery: 2020-2022.
- Validation: 2023.
- Untouched final holdout: 2024-01 through 2024-07.
- A rule needs distinct winner stocks and at least two winner sectors, not only
  repeated monthly positive rows.
- Corrected contract disclosures are removed before trailing contract counts.

The old score remains rejected in all periods: lift 0.688 train, 0.711
validation, and 0.814 holdout.

The most balanced confirmed historical signal is at least two original contract
disclosures in the trailing year plus point-in-time earnings improvement. Its
holdout results are 4.721x lift, 2.273% row precision, 36% winner-stock recall,
27.06% 3x incidence, 13.96% 5x incidence, and 61.1% median 24-month maximum
return. A liquidity version requiring at least KRW 1 billion average daily
turnover has 4.605x lift and 40% winner-stock recall.

Complementary non-contract families also survive the holdout: operating-profit
growth of at least 100%, revenue growth of at least 20% plus KRW 1 billion
20-day supply flow, and operating-profit turnaround plus 2x volume. These are
historical research signals, not production trading rules. Absolute tenbagger
precision remains low, and survivorship bias remains unresolved.

Artifacts and API:

```text
research_outputs/historical_tenbagger_signal_discovery.json
research_outputs/historical_tenbagger_signal_discovery.md
GET /api/tenbagger/historical-signal-discovery
```

The Monday 08:10 scheduler runs scoreboard, cause audit, and signal discovery
sequentially in that order.

### Precision correction (2026-08-09)

Row precision is not the operational recommendation hit rate because one stock
can occupy several monthly rows. The report now includes a first-alert-per-stock
metric and a fixed 15% promotion gate. The cross-sector stable precision tier is
two trailing-year original contracts, at least KRW 1 billion average daily
turnover, and point-in-time operating-profit growth of at least 50%.

Its 2024 holdout has 69 first alerts and four tenbagger winners: 5.80% precision
(Wilson 95% CI 2.28-13.98%), 36.23% 3x incidence, and 26.09% 5x incidence. It
therefore remains `research_candidate_only`; the top-level conclusion is
`holdout_signal_but_precision_insufficient`. A regularized multivariate logistic
ranking was also tested and rejected because holdout precision fell to 0-2.4%.
Neither this tier nor any discovered family may feed recommendations or trading.

## PostgreSQL Operational Cutover Completion (2026-08-10 12:16 KST)

The canonical operational database is now PostgreSQL. The backend, peak
monitor, and every Python collector spawned by the launchd service load
`runtime_pg_bootstrap/sitecustomize.py`. That bootstrap redirects only the
canonical `/Applications/stock_dashboard/stock.db` path through
`db_compat.PostgresCompatConnection`. Independent databases such as the ETF,
employment, and HS Trade Lab databases intentionally remain SQLite.

The legacy `stock.db` file is retained only as a frozen comparison and rollback
source. It is not an operational writer. `lsof` showed no process holding that
file after backend, frontend, monitor, and scheduler startup. PostgreSQL write
jobs also bypass the obsolete SQLite `flock` gate, and the old PostgreSQL delta
sync scheduler job is disabled.

Final verification commands:

```bash
venv/bin/python scripts/verify_postgres_cutover.py
venv/bin/python scripts/verify_tenbagger_postgres.py
lsof /Applications/stock_dashboard/stock.db
```

Verified result at `2026-08-10T12:15:55`:

- `is_postgres_primary=true`, one application schema (`public`).
- 219 PostgreSQL base tables; zero missing tables and zero tables behind the
  frozen SQLite source.
- 512 public indexes and a 24,048,466,967-byte PostgreSQL database.
- `price_history`: 8,183,861 rows, 4,274 codes, 2010-01-04 through 2026-08-10.
- `tenbagger_results`: 2,510 rows; all tenbagger parity and calculation probes
  passed.
- Automatic trading remains disabled.
- External rollback dump exists and is catalog-readable at
  `/Volumes/Realtek_NVME/stock_dashboard/postgres_public_pre_cutover_20260810.dump`
  (843,153,456 bytes).

Compatibility corrections completed during live validation include SQLite
date/datetime functions, `GLOB`, `PRAGMA table_info`, `sqlite_master`,
`INSERT OR IGNORE`, `INSERT OR REPLACE`, loose `GROUP BY` queries, aggregate
aliases in `HAVING`, implicit `rowid` ordering, and SQLite-only writer locks.
The dashboard statistics endpoint returned HTTP 200 from PostgreSQL, and the
startup screener completed trend, value, financial, supply-momentum, and Kiwoom
condition precomputation without falling back to `stock.db`.

### Recovery proof (2026-08-10)

Recovery is now executable and tested, not only documented. Use
`scripts/postgres_disaster_recovery.py` and
`docs/postgres_disaster_recovery.md`.

- Latest clean full snapshot dump: 2,730,785,610 bytes.
- SHA-256: `1cd0b359aa5151349320faadd4c59f0a1e1ba77e1bce51bbb6bc45b135ec8be8`.
- Catalog entries: 1,220.
- Full restore test: passed at 2026-08-10 13:02:08 KST.
- Restored public tables: 219; core count mismatches: zero.
- Restored macro contamination count: zero.
- The restore-test database was automatically removed after validation.
- A permanent disaster recovery restores to a new
  `stock_dashboard_recovered_*` database and never overwrites production.

Machine-readable evidence:

```text
research_outputs/postgres_cutover/disaster_recovery_latest.json
research_outputs/postgres_cutover/restore_test_latest.json
```

Operational continuity controls added after the restore proof:

- Sunday 05:10 KST: full exported-snapshot backup, manifest generation, catalog
  verification, and retention pruning.
- Daily 05:50 KST: full SHA-256 recalculation, catalog reopening, and freshness
  audit.
- Maximum accepted backup age: 8 days.
- Maximum accepted full restore-test age: 35 days.
- Daily audit failures create a deduplicated Telegram warning and a failed
  collection-run ledger entry.
- Scheduler startup verification: 90 jobs, including
  `PostgreSQL주간백업` and `PostgreSQL백업상태`.

Latest daily health evidence:

```text
research_outputs/postgres_cutover/recovery_health_latest.json
```

### Macro price contamination correction (2026-08-10)

A live-log review found symbol contamination that predated the PostgreSQL
cutover: DXY rows contained KRW-scale values, retired Treasury alternatives
contained unrelated prices, and several VIX rows contained yield-scale values.
The repair removed 769 objectively invalid rows, retired `^UST2Y` and `30Y=F`,
and backfilled three clean years for `DX-Y.NYB` (755 rows) and `2YY=F` (753
rows). A post-repair audit found zero invalid rows.

`macro_data_quality.py` now enforces symbol-specific plausible ranges in every
Yahoo collector and at the final market-price ingest boundary. The scheduler
runs `scripts/repair_macro_price_contamination.py` in audit-only mode every day
at 07:05 KST and sends a deduplicated warning if contamination reappears.

Evidence:

```text
research_outputs/postgres_cutover/macro_contamination_repair_latest.json
research_outputs/postgres_cutover/macro_clean_backfill_latest.json
```

### DART contract and tenbagger verification follow-up (2026-08-10 13:10 KST)

Live-log inspection found that `collectors/dart_contract_collector.py` still
used SQLite named parameters for its 22-column `INSERT OR IGNORE`. The
PostgreSQL compatibility connection interpreted the mapping as 22 positional
values while the translated query exposed no positional placeholders, so the
13:00 DART contract job stopped at its first disclosure. The insert now uses
22 explicit positional placeholders. A manual production refresh then scanned
175 disclosures and saved all seven new contract rows; `dart_contracts` now has
10,237 rows and is seven rows ahead of the frozen SQLite snapshot. The scheduler
job now re-raises collector errors so the run ledger records a real failure
instead of a false success.

The historical tenbagger audit now reads backtest evidence from PostgreSQL,
not the frozen `stock.db`. Recalculation preserves the prior conclusion:
`needs_revision`. The raw extreme-drawdown grades remain disabled, the
high-precision tier remains `research_candidate_only`, its 15% precision gate
is not met, and automatic trading remains disabled. Database and calculation
paths pass, but this is not evidence that the tenbagger logic is production
ready.

Machine-readable evidence:

```text
research_outputs/postgres_cutover/tenbagger_verification_latest.json
research_outputs/tenbagger_claude_change_audit_20260810.json
research_outputs/historical_tenbagger_signal_discovery.json
```

### PIT tenbagger rebuild and first-alert validation (2026-08-11)

The historical research universe was rebuilt in PostgreSQL as
`strategy_feature_snapshot_pit_v2`, including delisted-equity history and
point-in-time security/share intervals. The table has 187,543 rows, 2,691
stocks, zero duplicate keys, and zero security-interval violations. The old
55-point score is an anti-signal in train, validation, and 2024 evaluation.

No rule passed the 15% stock-level first-alert precision gate. The strongest
business-grounded research tag, 20-day net supply of at least KRW 1bn combined
with operating-profit growth of at least 100%, produced 2.13% 10x precision,
16.31% 3x hits, and 8.51% 5x hits in the 2024 evaluation period. It remains a
research tag and is not connected to recommendations or order execution.

Full methodology, reproduction commands, limitations, and evidence paths are
recorded in:

```text
docs/codex_handoff_tenbagger_pit_validation_20260811.md
```
