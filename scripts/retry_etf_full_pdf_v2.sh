#!/bin/zsh
set -u

ROOT="/Volumes/Realtek_NVME/stock_dashboard/runtime"
PYTHON="$ROOT/venv/bin/python"
LOG_DIR="$ROOT/ETF_check/logs"
LOCK_DIR="$ROOT/ETF_check/run/full_pdf.lock"
TARGET_DATE="$(date -v-1d '+%Y%m%d')"

mkdir -p "$LOG_DIR" "$ROOT/ETF_check/run"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "$(date '+%F %T') full PDF collection already running" >> "$LOG_DIR/full_pdf_cron.log"
  exit 0
fi
trap 'rmdir "$LOCK_DIR"' EXIT INT TERM

cd "$ROOT" || exit 1
PYTHONPATH="$ROOT/runtime_pg_bootstrap:$ROOT/ETF_check" "$PYTHON" \
  ETF_check/full_pdf_collector_v2.py --date "$TARGET_DATE" \
  >> "$LOG_DIR/full_pdf_cron.log" 2>&1
