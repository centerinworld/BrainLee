#!/bin/zsh
set -euo pipefail

ROOT="/Volumes/Realtek_NVME/stock_dashboard/runtime"
LABEL="com.stock-dashboard.etf-daily"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

mkdir -p "$HOME/Library/LaunchAgents"
cp "$ROOT/launchd/$LABEL.plist" "$TARGET"
chmod +x "$ROOT/scripts/run_etf_daily_pipeline.sh"
launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$TARGET"
launchctl enable "gui/$(id -u)/$LABEL"
echo "[stock-dashboard] ETF launchd installed: $LABEL"
