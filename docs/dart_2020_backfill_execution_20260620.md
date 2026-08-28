# DART 2020 Backfill Execution (2026-06-20)

## User instruction

- Do not limit DART collection to one year.
- Collect all available DART-backed datasets from 2020 onward.

## Scope

- Fiscal years: 2020, 2021, 2022, 2023, 2024, 2025, 2026
- DART keys: use all configured keys through `dart_key_manager.py`
- Execution mode: sequential `launchctl` job to avoid SQLite lock storms and DART quota bursts.

## Runner

- Script: `scripts/run_dart_2020_backfill_all.sh`
- Launch label: `com.stock-dashboard.dart2020backfill`
- Active run log directory:
  - `run/dart_2020_backfill_20260620_104113`

## Steps

1. `collect_dart_financial_batch.py --years 7`
2. `collect_dart_cashflow_batch.py --years 7 --fill-missing`
3. `scripts/collect_inventory_from_dart.py --year 2020 2021 2022 2023 2024 2025 2026 --quarter 1 2 3 4 --min-cap 0`
4. `python -m collectors.dart_material_purchase_collector --years 2020 2021 2022 2023 2024 2025 2026 --limit 10000`
5. `python -m collectors.dart_backlog_collector --year-from 2020 --year-to 2026`
6. `python -m collectors.dart_cost_collector --year-from 2020 --year-to 2026`
7. `scripts/collect_dart_segment_breakdown.py --years 2020,2021,2022,2023,2024,2025,2026 --limit 10000`
8. `scripts/collect_dart_ch_extra.py --limit 10000`

## Fixes made before launch

- Fixed `dart_key_manager.is_quota_error()` so pandas DataFrame returns are not misclassified as quota errors.
- Fixed `collect_dart_financial_batch.py` existing-row handling:
  - Skip existing DART-family rows.
  - If CFS is already occupied by another source, try OFS instead of throwing a unique-key error.
- Fixed `collectors/dart_material_purchase_collector.py` target selection so stocks with post-2022 data are still eligible for missing 2020-2021 backfill.

## Current status at launch verification

- Launch job is running.
- PID at verification: `2230`
- First step started successfully.
- Observed progress:
  - `000040`: 4 financial rows saved
  - `000050`: 4 financial rows saved
  - `000070`: 3 financial rows saved
  - `000080`: 1 financial row saved
