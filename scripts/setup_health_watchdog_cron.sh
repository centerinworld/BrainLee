#!/bin/zsh
set -euo pipefail

ROOT="/Volumes/Realtek_NVME/stock_dashboard/runtime"
SCRIPT="$ROOT/scripts/health_watchdog.sh"
CRON_LINE="*/10 * * * * $SCRIPT >/dev/null 2>&1"

existing="$(crontab -l 2>/dev/null || true)"
if echo "$existing" | grep -F "$SCRIPT" >/dev/null 2>&1; then
  echo "이미 등록됨: $SCRIPT"
  exit 0
fi

{
  echo "$existing"
  echo "$CRON_LINE"
} | crontab -

echo "등록 완료: $CRON_LINE"
