#!/usr/bin/env bash
set -euo pipefail

cd /Applications/stock_dashboard

PY="/Applications/stock_dashboard/venv/bin/python3"
LOG="/Applications/stock_dashboard/logs/backfill_short_public_20200101_20210217.screen.log"

{
  echo "[$(date '+%Y-%m-%dT%H:%M:%S')] short/public backfill start"
  echo "[$(date '+%Y-%m-%dT%H:%M:%S')] step 1: short/rank/sector gap 20210101~20210427"
  "$PY" -u collect_short_5years.py --mode all --start 20210101 --end 20210427

  echo "[$(date '+%Y-%m-%dT%H:%M:%S')] step 2: public-data backfill 20200101~20210217"
  "$PY" -u public_data_collector.py --backfill --start 20200101 --end 20210217
  echo "[$(date '+%Y-%m-%dT%H:%M:%S')] short/public backfill done"
} >> "$LOG" 2>&1
