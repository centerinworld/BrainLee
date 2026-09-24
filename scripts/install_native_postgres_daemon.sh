#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="/Volumes/Realtek_NVME/stock_dashboard/runtime"
LABEL="com.stock-dashboard.postgresql"
SOURCE="$PROJECT_ROOT/launchd/${LABEL}.system.plist"
TARGET="/Library/LaunchDaemons/${LABEL}.plist"
STAGED_SOURCE="/tmp/${LABEL}.$$.plist"

cleanup() {
  rm -f "$STAGED_SOURCE"
}
trap cleanup EXIT

plutil -lint "$SOURCE" >/dev/null

# A privileged AppleScript shell cannot read some external volumes unless the
# host app has Full Disk Access. Stage the validated plist on the system disk.
cp "$SOURCE" "$STAGED_SOURCE"
chmod 644 "$STAGED_SOURCE"

COMMAND="set -e; launchctl bootout system/$LABEL >/dev/null 2>&1 || true; /bin/cp '$STAGED_SOURCE' '$TARGET'; /usr/sbin/chown root:wheel '$TARGET'; /bin/chmod 644 '$TARGET'; launchctl bootstrap system '$TARGET'; launchctl enable system/$LABEL; launchctl kickstart -k system/$LABEL"
/usr/bin/osascript -e "do shell script \"$COMMAND\" with administrator privileges"

if ! cmp -s "$SOURCE" "$TARGET"; then
  echo "[stock-dashboard] installed plist verification failed" >&2
  exit 1
fi

for _ in {1..20}; do
  SERVICE_STATE="$(launchctl print "system/$LABEL" 2>/dev/null || true)"
  if grep -q "state = running" <<<"$SERVICE_STATE" && grep -q "pid = " <<<"$SERVICE_STATE"; then
    break
  fi
  sleep 1
done
if ! grep -q "state = running" <<<"${SERVICE_STATE:-}" || ! grep -q "pid = " <<<"${SERVICE_STATE:-}"; then
  echo "[stock-dashboard] launchd did not retain ownership of PostgreSQL" >&2
  exit 1
fi

"$PROJECT_ROOT/venv/bin/python" "$PROJECT_ROOT/scripts/check_postgres_ready.py" >/dev/null

echo "[stock-dashboard] native PostgreSQL system daemon installed: $LABEL"
