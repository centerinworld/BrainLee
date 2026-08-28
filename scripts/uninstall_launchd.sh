#!/bin/zsh
set -euo pipefail

LABEL="com.stock-dashboard.local"
TARGET_PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

launchctl bootout "gui/$(id -u)" "$TARGET_PLIST" >/dev/null 2>&1 || true
rm -f "$TARGET_PLIST"

echo "[stock-dashboard] launchd service removed: $LABEL"
