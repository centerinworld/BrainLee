# PostgreSQL Backup and Disaster Recovery

The production database and all backup/restore commands use native Homebrew
PostgreSQL 16. Docker is retained only as a temporary rollback source and is
not required by this procedure.

## Verified recovery point

Latest full backup:

```text
/Volumes/Realtek_NVME/stock_dashboard/postgres_backups/stock_dashboard_full_20260810_125134.dump
```

- Created: 2026-08-10 12:55:24 KST from one exported PostgreSQL snapshot.
- Size: 2,730,785,610 bytes.
- SHA-256: `1cd0b359aa5151349320faadd4c59f0a1e1ba77e1bce51bbb6bc45b135ec8be8`.
- Restore catalog: 1,220 entries.
- Public tables: 219; public indexes at backup time: 512; macro contamination: zero.
- Adjacent manifest: same path plus `.manifest.json`.
- Repository evidence: `research_outputs/postgres_cutover/disaster_recovery_latest.json`.

The backup was fully restored on 2026-08-10 13:02:08 KST into
`stock_dashboard_restore_verify_20260810_1256`. All 219 tables, all nine
core-table row counts, and the zero-contamination macro invariant matched the
backup manifest. The temporary database was then removed. Evidence is stored in
`research_outputs/postgres_cutover/restore_test_latest.json`.

## Verification

```bash
cd /Applications/stock_dashboard
venv/bin/python scripts/postgres_disaster_recovery.py verify \
  /Volumes/Realtek_NVME/stock_dashboard/postgres_backups/stock_dashboard_full_20260810_125134.dump

venv/bin/python scripts/postgres_disaster_recovery.py restore-test \
  /Volumes/Realtek_NVME/stock_dashboard/postgres_backups/stock_dashboard_full_20260810_125134.dump
```

`verify` checks byte size, SHA-256, and the `pg_restore` catalog. `restore-test`
creates an isolated database, restores the complete dump, compares the public
table count and core row counts, records evidence, and removes the test DB.

## Create a fresh backup

```bash
venv/bin/python scripts/postgres_disaster_recovery.py backup
```

New backups are written below:

```text
/Volumes/Realtek_NVME/stock_dashboard/postgresql16/backups
```

The live service does not need to stop. The command exports one repeatable-read
snapshot and uses it for both `pg_dump` and manifest counts, then atomically
renames the completed `.partial` file.

The application scheduler runs this backup every Sunday at 05:10 KST. It then
runs `prune --apply`, retaining the latest eight backups, the latest backup for
up to twelve months, the latest manifest backup, and the most recently
restore-tested backup.

Every day at 05:50 KST the scheduler runs `audit`. It recalculates the full
SHA-256, reopens the restore catalog, and checks freshness. A backup older than
8 days or a restore test older than 35 days fails the audit and sends one
deduplicated Telegram warning for that day. The latest audit evidence is stored
at `research_outputs/postgres_cutover/recovery_health_latest.json`.

## Disaster recovery without overwriting production

Restore into a new database. The tool refuses the operational database name,
refuses names without the recovery prefix, and refuses to replace an existing
recovery database.

```bash
venv/bin/python scripts/postgres_disaster_recovery.py restore \
  /Volumes/Realtek_NVME/stock_dashboard/postgres_backups/stock_dashboard_full_20260810_125134.dump \
  --target stock_dashboard_recovered_20260810
```

After successful validation:

1. Stop the dashboard launchd service.
2. Change only the database name in `POSTGRES_DATABASE_URL` from
   `stock_dashboard` to `stock_dashboard_recovered_20260810`.
3. Start the service and run `scripts/verify_postgres_cutover.py` plus the main
   API smoke tests.
4. Keep the original database unchanged until the recovered service has passed
   operational verification.

Never restore directly over `stock_dashboard`. Recovery is performed by
creating a new database and switching the connection URL, which preserves the
previous database for rollback.
