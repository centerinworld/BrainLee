#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="/Applications/stock_dashboard"
LABEL="com.stock-dashboard.local"
SOURCE_PLIST="$PROJECT_ROOT/launchd/${LABEL}.plist"
TARGET_DIR="$HOME/Library/LaunchAgents"
TARGET_PLIST="$TARGET_DIR/${LABEL}.plist"

mkdir -p "$TARGET_DIR"
cp "$SOURCE_PLIST" "$TARGET_PLIST"

launchctl bootout "gui/$(id -u)" "$TARGET_PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$TARGET_PLIST"
launchctl enable "gui/$(id -u)/$LABEL"
launchctl kickstart -k "gui/$(id -u)/$LABEL"

echo "[stock-dashboard] launchd service installed: $LABEL"
echo "[stock-dashboard] frontend: http://127.0.0.1:5173"
echo "[stock-dashboard] backend : http://127.0.0.1:8000/docs"
