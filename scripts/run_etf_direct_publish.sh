#!/bin/zsh
set -u

ROOT="/Volumes/Realtek_NVME/stock_dashboard/runtime"
PYTHON="$ROOT/venv/bin/python"
LOG="$ROOT/ETF_check/logs/direct_publish.log"
TARGET_DATE="${1:-}"

mkdir -p "$ROOT/ETF_check/logs"
exec >> "$LOG" 2>&1

cd "$ROOT" || exit 1
export PYTHONPATH="$ROOT/runtime_pg_bootstrap:$ROOT/ETF_check"
echo "[$(date '+%F %T')] START base_date=${TARGET_DATE:-auto}"
args=()
if [[ -n "$TARGET_DATE" ]]; then
  args=(--date "$TARGET_DATE")
fi
"$PYTHON" ETF_check/publish_direct_stock_daily.py "${args[@]}"
code=$?
echo "[$(date '+%F %T')] END base_date=${TARGET_DATE:-auto} exit=$code"
exit $code
