#!/bin/zsh
set -u
ROOT="/Volumes/Realtek_NVME/stock_dashboard/runtime"
PYTHON="$ROOT/venv/bin/python"
TARGET_DATE="${1:-$(date -v-1d '+%Y%m%d')}"
TARGET_DATE="$(cd "$ROOT" && PYTHONPATH="$ROOT" "$PYTHON" -c '
import sys
from datetime import datetime,timedelta
from trading_calendar import is_kr_trading_day
d=datetime.strptime(sys.argv[1],"%Y%m%d").date()
while not is_kr_trading_day(d): d-=timedelta(days=1)
print(d.strftime("%Y%m%d"))
' "$TARGET_DATE")"
if [[ "$(sqlite3 "$ROOT/ETF_check/etf_check.db" "SELECT COUNT(*) FROM etf_direct_stock_publication WHERE base_date='$TARGET_DATE' AND status='published';")" == "1" ]]; then
  echo "$(date '+%F %T') SKIP base_date=$TARGET_DATE already published" >> "$ROOT/ETF_check/logs/etf_scale_collection.log"
  exit 0
fi
cd "$ROOT" || exit 1
PYTHONPATH="$ROOT/ETF_check" "$PYTHON" ETF_check/etf_scale_collector.py --date "$TARGET_DATE" \
  >> "$ROOT/ETF_check/logs/etf_scale_collection.log" 2>&1
