#!/bin/zsh
set -u

ROOT="/Volumes/Realtek_NVME/stock_dashboard/runtime"
PYTHON="$ROOT/venv/bin/python"
DB="$ROOT/ETF_check/etf_check.db"
LOG="$ROOT/ETF_check/logs/direct_publish.log"
TARGET_DATE="${1:-}"

mkdir -p "$ROOT/ETF_check/logs"
exec >> "$LOG" 2>&1

if [[ -z "$TARGET_DATE" ]]; then
  TARGET_DATE="$(sqlite3 "$DB" "SELECT MAX(base_date) FROM etf_source_parity_daily;")"
fi
if [[ -z "$TARGET_DATE" ]]; then
  echo "[$(date '+%F %T')] SKIP no validated ETF date"
  exit 1
fi

cd "$ROOT" || exit 1
export PYTHONPATH="$ROOT/runtime_pg_bootstrap:$ROOT/ETF_check"
echo "[$(date '+%F %T')] START base_date=$TARGET_DATE"
"$PYTHON" ETF_check/publish_direct_stock_daily.py --date "$TARGET_DATE"
code=$?
echo "[$(date '+%F %T')] END base_date=$TARGET_DATE exit=$code"
exit $code
