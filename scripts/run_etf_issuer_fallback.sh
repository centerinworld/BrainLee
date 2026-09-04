#!/bin/zsh
set -u

ROOT="/Volumes/Realtek_NVME/stock_dashboard/runtime"
TARGET_DATE="$(date '+%Y%m%d')"
cd "$ROOT" || exit 1
PYTHONPATH="$ROOT/ETF_check" "$ROOT/venv/bin/python" \
  ETF_check/issuer_pdf_fallback_v2.py --date "$TARGET_DATE" \
  >> "$ROOT/ETF_check/logs/issuer_fallback.log" 2>&1
