#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="/Volumes/Realtek_NVME/stock_dashboard/runtime"
LABEL="com.stock-dashboard.postgresql.user"
SOURCE="$PROJECT_ROOT/launchd/${LABEL}.plist"
TARGET="$HOME/Library/LaunchAgents/${LABEL}.plist"
PG_DATA_DIR="/Volumes/Realtek_NVME/stock_dashboard/postgresql16/data"
PG_CTL="/opt/homebrew/opt/postgresql@16/bin/pg_ctl"

plutil -lint "$SOURCE" >/dev/null
mkdir -p "$HOME/Library/LaunchAgents"
cp "$SOURCE" "$TARGET"

launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
if "$PG_CTL" -D "$PG_DATA_DIR" status >/dev/null 2>&1; then
  "$PG_CTL" -D "$PG_DATA_DIR" stop -m fast -w
fi
launchctl bootstrap "gui/$(id -u)" "$TARGET"
launchctl enable "gui/$(id -u)/$LABEL"
launchctl kickstart -k "gui/$(id -u)/$LABEL"

for _ in {1..30}; do
  SERVICE_STATE="$(launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null || true)"
  if grep -q "state = running" <<<"$SERVICE_STATE" && grep -q "pid = " <<<"$SERVICE_STATE"; then
    "$PROJECT_ROOT/venv/bin/python" "$PROJECT_ROOT/scripts/check_postgres_ready.py" >/dev/null
    echo "[stock-dashboard] user PostgreSQL agent installed: $LABEL"
    exit 0
  fi
  sleep 1
done

echo "[stock-dashboard] user PostgreSQL agent failed to retain the server" >&2
exit 1
