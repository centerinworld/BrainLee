#!/usr/bin/env bash
set -euo pipefail

ROOT="/Volumes/Realtek_NVME/stock_dashboard/runtime"
LABEL="com.stock-dashboard.dart-insider-doc-2020-2023"
LOG_DIR="$ROOT/run/strategy_data_backfill_queue"
mkdir -p "$LOG_DIR"

echo "$(date '+%F %T') waiting for $LABEL" >> "$LOG_DIR/queue.log"
while launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -q "state = running"; do
  sleep 60
done

echo "$(date '+%F %T') starting backlog 2021-2022 backfill" >> "$LOG_DIR/queue.log"
cd "$ROOT"
"$ROOT/venv/bin/python" "$ROOT/scripts/backfill_backlog_2021_2022.py" >> "$LOG_DIR/backlog.out" 2>> "$LOG_DIR/backlog.err"
echo "$(date '+%F %T') backlog backfill finished" >> "$LOG_DIR/queue.log"
