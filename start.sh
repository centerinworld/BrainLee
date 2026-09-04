#!/bin/zsh
# Start the local dashboard on fixed ports after cleaning stale servers.
set -euo pipefail

PROJECT_ROOT="/Volumes/Realtek_NVME/stock_dashboard/runtime"
BACKEND_PORT=8000
FRONTEND_PORT=5173
LOG_DIR="$PROJECT_ROOT/logs"
BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"
LAUNCHD_LABEL="com.stock-dashboard.local"
LAUNCHD_PLIST="$HOME/Library/LaunchAgents/${LAUNCHD_LABEL}.plist"

mkdir -p "$LOG_DIR"

if [ -f "$LAUNCHD_PLIST" ]; then
  echo "[stock-dashboard] launchd service is installed; reloading it..."
  launchctl bootout "gui/$(id -u)" "$LAUNCHD_PLIST" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$(id -u)" "$LAUNCHD_PLIST"
  launchctl enable "gui/$(id -u)/${LAUNCHD_LABEL}" >/dev/null 2>&1 || true
  for _ in {1..40}; do
    if curl -fsS "http://127.0.0.1:${BACKEND_PORT}/docs" >/dev/null 2>&1 \
      && curl -fsS "http://127.0.0.1:${FRONTEND_PORT}/" >/dev/null 2>&1; then
      echo "[stock-dashboard] ready"
      echo "  backend : http://127.0.0.1:${BACKEND_PORT}"
      echo "  frontend: http://127.0.0.1:${FRONTEND_PORT}"
      exit 0
    fi
    sleep 1
  done
  echo "[stock-dashboard] launchd service did not become ready. Check $LOG_DIR"
  exit 1
fi

echo "[stock-dashboard] stopping stale local servers..."
"$PROJECT_ROOT/stop.sh"

echo "[stock-dashboard] starting backend on ${BACKEND_PORT}..."
cd "$PROJECT_ROOT"
nohup "$PROJECT_ROOT/venv/bin/uvicorn" main:app \
  --host 127.0.0.1 \
  --port "$BACKEND_PORT" \
  --workers 1 \
  >> "$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!
echo "$BACKEND_PID" > "$LOG_DIR/backend.pid"
disown "$BACKEND_PID" 2>/dev/null || true

echo "[stock-dashboard] waiting for backend..."
for _ in {1..40}; do
  if curl -fsS "http://127.0.0.1:${BACKEND_PORT}/docs" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
if ! curl -fsS "http://127.0.0.1:${BACKEND_PORT}/docs" >/dev/null 2>&1; then
  echo "[stock-dashboard] backend did not become ready. Recent log:"
  tail -n 80 "$BACKEND_LOG" || true
  exit 1
fi

echo "[stock-dashboard] building frontend..."
cd "$PROJECT_ROOT/frontend"
rm -rf dist
npm run build

echo "[stock-dashboard] starting frontend preview on ${FRONTEND_PORT}..."
nohup npm run preview \
  >> "$FRONTEND_LOG" 2>&1 &
FRONTEND_PID=$!
echo "$FRONTEND_PID" > "$LOG_DIR/frontend.pid"
disown "$FRONTEND_PID" 2>/dev/null || true

echo "[stock-dashboard] waiting for frontend..."
for _ in {1..40}; do
  if curl -fsS "http://127.0.0.1:${FRONTEND_PORT}/" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
if ! curl -fsS "http://127.0.0.1:${FRONTEND_PORT}/" >/dev/null 2>&1; then
  echo "[stock-dashboard] frontend did not become ready. Recent log:"
  tail -n 80 "$FRONTEND_LOG" || true
  exit 1
fi

echo "[stock-dashboard] ready"
echo "  backend : http://127.0.0.1:${BACKEND_PORT}"
echo "  frontend: http://127.0.0.1:${FRONTEND_PORT}"
echo "  logs    : $LOG_DIR"
