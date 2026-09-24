# Codex Handoff: Runtime Hardening (2026-09-06)

## PostgreSQL service cutover

- Root cause: a system LaunchDaemon could not open paths on the external
  Realtek volume and exited with `EX_CONFIG`. The old installer also continued
  after its privileged external-volume copy failed, so it printed a false
  success message.
- Production now uses the user LaunchAgent
  `com.stock-dashboard.postgresql.user`. Its source is
  `launchd/com.stock-dashboard.postgresql.user.plist`; the installed copy is in
  `~/Library/LaunchAgents`.
- The authoritative data directory remains
  `/Volumes/Realtek_NVME/stock_dashboard/postgresql16/data`.
- The obsolete system job and
  `/Library/LaunchDaemons/com.stock-dashboard.postgresql.plist` were removed.
- `scripts/serve_foreground.sh` waits for the database and starts the same
  external cluster with `pg_ctl` only if no PostgreSQL service is available.
- A real KeepAlive test replaced PID 52795 with PID 53425 and passed
  `scripts/check_postgres_ready.py`.
- Reinstall/repair command: `./scripts/install_user_postgres_agent.sh`.

## PostgreSQL compatibility repair

- `db_compat.py` no longer probes every INSERT with `RETURNING id`.
- It checks `information_schema.columns` once per table and connection, caches
  the result, and requests `lastrowid` only for tables that have an `id` column.
- Live transaction verification returned `1` for an id table and `None` for a
  non-id table, then rolled the probe transaction back.
- No new `column "id" does not exist` errors appeared after backend restart.

## Data and performance verification

- Full page data-quality audit: 34/34 OK, zero missing, stale, or unstable
  datasets. Report: `research_outputs/all_page_data_quality_20260906.md`.
- Core contracts are healthy: price 2,666 (2026-09-04), investor flow 2,668
  (2026-09-04), foreign holding 2,668 (2026-09-04), short balance 2,704
  (2026-09-03; accepted source lag).
- ADR no longer joins the previous calendar day. It uses the previous trading
  observation with `LAG`, fixing Monday/holiday distortion and reducing the
  measured query path from about 13 seconds to 0.69 seconds including Python
  startup.
- Final HTTP checks: backend 200 in 0.18 seconds; frontend 200 in 0.002 seconds.
- The obsolete 23 GB repetitive LaunchDaemon error log was preserved as gzip:
  `logs/postgresql.launchd.err.log.gz` (482 MB), SHA-256
  `4abe238d447f1db666936320d37b655af7371d6783f0319b71b998a2c113c9c2`.

## Strategy-center evidence

- `contract_momentum` now accepts and persists `data_asof_ts` and filters
  `dart_contracts.created_at`. Coverage is 10,388/10,388 non-null timestamps.
- Two independent fixed-snapshot six-window runs were identical for return,
  trade count, and MDD. Evidence:
  `research_outputs/deterministic_contract_momentum_20260906.json` and
  `research_outputs/deterministic_contract_momentum_repeat_20260906.json`.
- Result: mean return 25.165%, 4/6 positive windows, worst MDD -19.58%.
- This result is deliberately not auto-selected into Strategy Center. It is
  completed but unreviewed; selection and true future-forward evidence remain
  separate governance decisions.
- Current live-readiness blocker remains
  `BACKTEST_EVIDENCE_NOT_FULL_PIT_OR_FORWARD`. Eleven independent engines still
  lack a complete snapshot contract after this change, and actual future data
  cannot be manufactured retrospectively.

## Verification commands

```bash
launchctl print gui/$(id -u)/com.stock-dashboard.postgresql.user
./venv/bin/python scripts/check_postgres_ready.py
./venv/bin/python -m unittest discover -s tests -p 'test_*.py'
./venv/bin/python scripts/audit_all_page_data_quality.py
./venv/bin/python scripts/audit_strategy_center_live_data_readiness.py
```

The standard test suite passed 93/93 with `ResourceWarning` promoted to an
error. ETF logging now owns one module handler, and `database.py` disposes the
SQLAlchemy engine at process exit.
