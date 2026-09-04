#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="/Volumes/Realtek_NVME/stock_dashboard/runtime"
APP_LABEL="com.stock-dashboard.local"
TARGET_DIR="$HOME/Library/LaunchAgents"

mkdir -p "$TARGET_DIR"

if ! launchctl print system/com.stock-dashboard.postgresql >/dev/null 2>&1; then
  echo "[stock-dashboard] native PostgreSQL system daemon is not installed" >&2
  echo "[stock-dashboard] run: $PROJECT_ROOT/scripts/install_native_postgres_daemon.sh" >&2
  exit 1
fi

for LABEL in "$APP_LABEL"; do
  SOURCE_PLIST="$PROJECT_ROOT/launchd/${LABEL}.plist"
  TARGET_PLIST="$TARGET_DIR/${LABEL}.plist"
  cp "$SOURCE_PLIST" "$TARGET_PLIST"
  launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$(id -u)" "$TARGET_PLIST"
  launchctl enable "gui/$(id -u)/$LABEL"
  launchctl kickstart -k "gui/$(id -u)/$LABEL"
done

echo "[stock-dashboard] system service ready: com.stock-dashboard.postgresql"
echo "[stock-dashboard] launchd service installed: $APP_LABEL"
echo "[stock-dashboard] frontend: http://127.0.0.1:5173"
echo "[stock-dashboard] backend : http://127.0.0.1:8000/docs"
