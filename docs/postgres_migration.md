# Postgres Migration

This project is moving away from the single `stock.db` SQLite file.

## Current State (native cutover completed 2026-08-15)

- Production PostgreSQL 16 is installed with Homebrew.
- The production data directory is
  `/Volumes/Realtek_NVME/stock_dashboard/postgresql16/data`.
- `com.stock-dashboard.postgresql` owns native database startup.
- The app reads `POSTGRES_DATABASE_URL` before `DATABASE_URL`.
- SQLAlchemy-backed app paths use Postgres when `POSTGRES_DATABASE_URL` is set.
- The canonical operational database is PostgreSQL.
- Service Python processes route the canonical legacy `stock.db` path to PostgreSQL.
- The legacy SQLite file is retained only for comparison and rollback evidence.
- Independent ETF, employment, and HS Trade Lab SQLite databases remain intentional.
- `docker-compose.postgres.yml` is disabled behind the
  `legacy-docker-rollback` profile and is not part of normal runtime.

## Legacy Docker rollback source

```bash
docker compose -f docker-compose.postgres.yml --profile legacy-docker-rollback up -d
```

Production uses native Homebrew PostgreSQL 16 with its data directory at
`/Volumes/Realtek_NVME/stock_dashboard/postgresql16/data`. See
`docs/codex_handoff_native_postgres_20260815.md`.

Default local URL:

```bash
postgresql+psycopg://stock_dashboard:stock_dashboard_local@127.0.0.1:5432/stock_dashboard
```

## Verify

```bash
venv/bin/python scripts/check_postgres_ready.py
venv/bin/python - <<'PY'
from database import engine
print(engine.dialect.name)
PY
```

## Migrate SQLite Tables

```bash
venv/bin/python scripts/migrate_sqlite_to_postgres.py --drop \
  --only stock_universe price_history financial_data cash_flow_data dilution_events \
  tenbagger_results tenbagger_ai_analysis tenbagger_daily_alerts \
  --batch-size 10000
```

## Backup and restore

See `docs/postgres_disaster_recovery.md`. The recovery tool creates consistent
snapshot backups, validates SHA-256 and the restore catalog, performs isolated
full restore tests, and refuses to overwrite the operational database.
