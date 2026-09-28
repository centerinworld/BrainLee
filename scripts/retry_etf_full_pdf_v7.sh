#!/bin/zsh
set -u
ROOT="/Volumes/Realtek_NVME/stock_dashboard/runtime"; PYTHON="$ROOT/venv/bin/python"; LOG_DIR="$ROOT/ETF_check/logs"; LOCK_DIR="$ROOT/ETF_check/run/full_pdf.lock"; TARGET_DATE="${1:-$(date -v-1d '+%Y%m%d')}"
TARGET_DATE="$(cd "$ROOT" && PYTHONPATH="$ROOT" "$PYTHON" -c '
import sys
from datetime import datetime,timedelta
from trading_calendar import is_kr_trading_day
d=datetime.strptime(sys.argv[1],"%Y%m%d").date()
while not is_kr_trading_day(d): d-=timedelta(days=1)
print(d.strftime("%Y%m%d"))
' "$TARGET_DATE")"
mkdir -p "$LOG_DIR" "$ROOT/ETF_check/run"
if [[ "$(sqlite3 "$ROOT/ETF_check/etf_check.db" "SELECT COUNT(*) FROM etf_direct_stock_publication WHERE base_date='$TARGET_DATE' AND status='published';")" == "1" ]]; then
  echo "$(date '+%F %T') SKIP base_date=$TARGET_DATE already published" >> "$LOG_DIR/full_pdf_cron.log"
  exit 0
fi
if ! mkdir "$LOCK_DIR" 2>/dev/null; then echo "$(date '+%F %T') full PDF collection already running" >> "$LOG_DIR/full_pdf_cron.log"; exit 0; fi
trap 'rmdir "$LOCK_DIR"' EXIT INT TERM
cd "$ROOT" || exit 1
PYTHONPATH="$ROOT/runtime_pg_bootstrap:$ROOT/ETF_check" "$PYTHON" ETF_check/full_pdf_collector_v7.py --date "$TARGET_DATE" >> "$LOG_DIR/full_pdf_cron.log" 2>&1
