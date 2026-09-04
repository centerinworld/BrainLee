# Codex: BigQuery Low-Cost Trigger Discovery Lab (2026-07-28)

## What Was Implemented

### 1. Low-cost BigQuery mode

Modified:

- `/Applications/stock_dashboard/bigquery_sync.py`
- `/Applications/stock_dashboard/scheduler.py`

New BigQuery sync mode:

```bash
/Applications/stock_dashboard/venv/bin/python bigquery_sync.py --mode daily-lite --days 7
```

Scheduler now runs:

```bash
bigquery_sync.py --mode daily-lite --days 7
```

instead of the heavier full daily sync.

Cost controls:

- `BQ_PROJECT_ID` and `BQ_DATASET_ID` can now be overridden by environment.
- `BQ_MAX_BYTES_BILLED` controls query cost guard.
- Default max bytes billed: `20GiB` per query.
- `daily-lite` uploads only core Strategy/Trigger Discovery tables plus recent
  `price_history`.
- Large raw short-selling tables were uploaded once during this run, then
  removed from the repeated `daily-lite` table list.

### 2. Trigger Discovery Lab tables

Added:

- `/Applications/stock_dashboard/scripts/build_trigger_discovery_lab.py`

Generated local SQLite tables:

- `trigger_discovery_events`
- `trigger_discovery_stock_links`
- `trigger_discovery_forward_returns`

Each event includes:

- `event_date`
- `available_date`
- `trigger_key`
- `trigger_name`
- source
- direction/strength
- stock/sector links where available

Forward returns are calculated only when the stock has a tradable close within a
short fill window. Future `available_date` rows are excluded.

Local build command:

```bash
/Applications/stock_dashboard/venv/bin/python scripts/build_trigger_discovery_lab.py --start 2020-01-01
```

Final local build result:

```json
{
  "quant_events": 103523,
  "order_events": 3767,
  "backlog_events": 1428,
  "trigger_discovery_events": 108652,
  "trigger_discovery_stock_links": 313596,
  "trigger_discovery_forward_returns": 878303
}
```

QA:

- `available_date` min/max: `2020-01-20` to `2026-07-28`
- future `available_date` rows: `0`
- forward return rows:
  - 20d: `301,797`
  - 60d: `295,295`
  - 120d: `281,211`

### 3. BigQuery Trigger Discovery views

Added view creator:

```bash
/Applications/stock_dashboard/venv/bin/python bigquery_sync.py --mode trigger-views
```

Created views:

- `v_trigger_discovery_scorecard`
- `v_trigger_sector_scorecard`
- `v_trigger_recent_candidates`

Query verification:

```text
v_trigger_discovery_scorecard 293
v_trigger_sector_scorecard 746
v_trigger_recent_candidates 9320
```

### 4. BigQuery upload run

Executed:

```bash
/Applications/stock_dashboard/venv/bin/python bigquery_sync.py --mode daily-lite --skip-trigger-lab-build --days 7
```

Completed successfully except for one view type issue, then fixed and reran:

```bash
/Applications/stock_dashboard/venv/bin/python bigquery_sync.py --mode trigger-views
```

Final status:

- data upload: success
- Trigger Discovery views: success

## Current BigQuery Storage Estimate

Using BigQuery Table API:

```json
{
  "tables": 219,
  "rows": 43872947,
  "logical_gib": 6.553,
  "storage_usd_month_est": 0.1507,
  "storage_krw_month_est_1400": 211
}
```

Interpretation:

- Current storage cost is roughly a few hundred KRW/month.
- Query cost should stay near free-tier if we keep partition/cluster discipline
  and use `maximum_bytes_billed`.
- Biggest tables are `kiwoom_credit_balance`, `broker_program_stock_daily`,
  `kiwoom_investor_daily`, `price_history`, and short-selling raw tables.

## Next Improvements

1. Replace repeated raw large-table uploads with smaller feature tables:
   - program net buy 5/20/60d
   - short balance/rank change 5/20/60d
   - lending balance change
2. Add BigQuery scheduled query or local job:
   - refresh `v_trigger_recent_candidates`
   - export top candidates back into local `stock.db`
   - show in Strategy Center as `Trigger Discovery Lab`
3. Add overfitting controls:
   - train/test split by period
   - minimum sample count
   - loss30 guard
   - deflated/penalized score for repeated hypothesis testing
4. Add paid consensus data when available:
   - expected EPS
   - target price revisions
   - forward PER
   - sector estimate revisions

