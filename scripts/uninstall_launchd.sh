#!/bin/zsh
set -euo pipefail

for LABEL in com.stock-dashboard.local; do
  TARGET_PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
  launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
  rm -f "$TARGET_PLIST"
  echo "[stock-dashboard] launchd service removed: $LABEL"
done

echo "[stock-dashboard] PostgreSQL system daemon is retained"
