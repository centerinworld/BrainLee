# Native PostgreSQL cutover handoff (2026-08-15)

## Decision

The canonical `stock_dashboard` PostgreSQL database now runs as a native
Homebrew PostgreSQL 16 service. Docker Desktop is no longer in the production
runtime path. Database files, WAL, logs, and migration backups are stored on
the external APFS volume.

- PostgreSQL binary: `/opt/homebrew/opt/postgresql@16/bin/postgres`
- Data directory: `/Volumes/Realtek_NVME/stock_dashboard/postgresql16/data`
- Backup directory: `/Volumes/Realtek_NVME/stock_dashboard/postgresql16/backups`
- System LaunchDaemon label: `com.stock-dashboard.postgresql`
- App service label: `com.stock-dashboard.local`
- Listen address: `127.0.0.1:5432`
- PostgreSQL data checksums: enabled
- Authentication: SCRAM-SHA-256

The app URL does not change because both runtimes use `127.0.0.1:5432`.
`scripts/check_postgres_ready.py` rejects a server whose `data_directory` is
not the external native path, preventing an accidental fallback to Docker.

## Migration evidence

- Docker source: PostgreSQL 16.14, approximately 24 GB.
- Native target: PostgreSQL 16.15.
- Consistent directory-format backup:
  `/Volumes/Realtek_NVME/stock_dashboard/postgresql16/backups/stock_dashboard_docker_20260815_065022.dir`
- Backup size: approximately 2.1 GB compressed.
- Backup integrity: 257 files passed SHA-256 verification.
- Final signal delta backup:
  `/Volumes/Realtek_NVME/stock_dashboard/postgresql16/backups/signal_result_final_20260815_0701.dump`
- Exact parity: 256 public tables and 56,512,860 rows.
- Catalog parity: 2 views, 560 indexes, 238 constraints, 0 sequences, and 3 functions.
- Invalid or not-ready indexes on the target: 0.
- Native full backup:
  `/Volumes/Realtek_NVME/stock_dashboard/postgresql16/backups/stock_dashboard_full_20260815_071819.dump`
- Native backup size: 1,879,396,704 bytes.
- Native backup SHA-256:
  `c1418a47d3876a2f2e073451bf90d1e7bcf4d47a6648691cdbea1a258e721355`.
- Native backup restore-test: passed at 2026-08-15 07:28 KST; 256 tables,
  all nine core counts matched, zero macro contamination, temporary database removed.

Two indexes created on the Docker source after the full snapshot were recreated
on the native target:

- `ix_broker_program_stock_daily_code_dt`
- `ix_price_history_code_date_posclose`

## Service operations

Install or refresh both launch agents:

```bash
cd /Applications/stock_dashboard
./scripts/install_native_postgres_daemon.sh
./scripts/install_launchd.sh
```

Verify the native database identity:

```bash
/Applications/stock_dashboard/venv/bin/python scripts/check_postgres_ready.py
launchctl list | grep 'com.stock-dashboard'
```

## Rollback

Do not delete the Docker volume or the migration backups until the native
service has passed sustained operation and a reboot test. To roll back:

1. Unload `com.stock-dashboard.local` and `com.stock-dashboard.postgresql`.
2. Start `docker-compose.postgres.yml` and confirm it owns port 5432.
3. Start only `com.stock-dashboard.local` after temporarily setting
   `POSTGRES_EXPECTED_DATA_DIRECTORY` to the Docker data directory, or revert
   the native-directory identity gate.

The preferred recovery path is restoration from the SHA-256-verified logical
backup into a clean PostgreSQL 16 cluster, not copying live database files.
