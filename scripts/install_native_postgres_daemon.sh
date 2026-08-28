#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="/Applications/stock_dashboard"
LABEL="com.stock-dashboard.postgresql"
SOURCE="$PROJECT_ROOT/launchd/${LABEL}.system.plist"
TARGET="/Library/LaunchDaemons/${LABEL}.plist"

plutil -lint "$SOURCE" >/dev/null

COMMAND="launchctl bootout system/$LABEL >/dev/null 2>&1 || true; cp '$SOURCE' '$TARGET'; chown root:wheel '$TARGET'; chmod 644 '$TARGET'; launchctl bootstrap system '$TARGET'; launchctl enable system/$LABEL; launchctl kickstart -k system/$LABEL"
/usr/bin/osascript -e "do shell script \"$COMMAND\" with administrator privileges"

echo "[stock-dashboard] native PostgreSQL system daemon installed: $LABEL"
