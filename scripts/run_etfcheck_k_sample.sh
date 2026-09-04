#!/bin/zsh
set -u
ROOT="/Volumes/Realtek_NVME/stock_dashboard/runtime";cd "$ROOT"||exit 1
PYTHONPATH="$ROOT/ETF_check" "$ROOT/venv/bin/python" ETF_check/etfcheck_k_sample_collector.py >> "$ROOT/ETF_check/logs/etfcheck_k_sample.log" 2>&1
