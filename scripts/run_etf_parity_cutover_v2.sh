#!/bin/zsh
set -u
ROOT="/Volumes/Realtek_NVME/stock_dashboard/runtime";cd "$ROOT"||exit 1
PYTHONPATH="$ROOT/ETF_check" "$ROOT/venv/bin/python" ETF_check/etf_parity_cutover_v2.py >> "$ROOT/ETF_check/logs/etf_parity_cutover.log" 2>&1
