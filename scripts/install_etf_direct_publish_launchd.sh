#!/bin/zsh
set -eu

ROOT="/Volumes/Realtek_NVME/stock_dashboard/runtime"
LABEL="com.stock-dashboard.etf-direct-publish"
SOURCE="$ROOT/launchd/$LABEL.plist"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

mkdir -p "$HOME/Library/LaunchAgents"
cp "$SOURCE" "$TARGET"
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$TARGET"
launchctl enable "$DOMAIN/$LABEL"
echo "installed $LABEL"
