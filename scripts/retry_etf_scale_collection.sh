#!/bin/zsh
set -u
ROOT="/Volumes/Realtek_NVME/stock_dashboard/runtime"
TARGET_DATE="$(date -v-1d '+%Y%m%d')"
cd "$ROOT" || exit 1
PYTHONPATH="$ROOT/ETF_check" "$ROOT/venv/bin/python" ETF_check/etf_scale_collector.py --date "$TARGET_DATE" \
  >> "$ROOT/ETF_check/logs/etf_scale_collection.log" 2>&1
