#!/bin/zsh
# Foreground supervisor for launchd. If either server exits, stop both and let
# launchd restart this script.
set -euo pipefail

PROJECT_ROOT="/Applications/stock_dashboard"
BACKEND_PORT=8000
FRONTEND_PORT=5173
LOG_DIR="$PROJECT_ROOT/logs"
BACKEND_LOG="$LOG_DIR/backend.launchd.log"
FRONTEND_LOG="$LOG_DIR/frontend.launchd.log"

mkdir -p "$LOG_DIR"

cleanup() {
  trap - INT TERM EXIT
  [ -n "${BACKEND_PID:-}" ] && kill -TERM "$BACKEND_PID" 2>/dev/null || true
  [ -n "${FRONTEND_PID:-}" ] && kill -TERM "$FRONTEND_PID" 2>/dev/null || true
  "$PROJECT_ROOT/stop.sh" --processes-only >/dev/null 2>&1 || true
}

trap cleanup INT TERM EXIT

"$PROJECT_ROOT/stop.sh" --processes-only

cd "$PROJECT_ROOT"
"$PROJECT_ROOT/venv/bin/uvicorn" main:app \
  --host 127.0.0.1 \
  --port "$BACKEND_PORT" \
  --workers 1 \
  >> "$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!
echo "$BACKEND_PID" > "$LOG_DIR/backend.pid"

for _ in {1..40}; do
  curl -fsS "http://127.0.0.1:${BACKEND_PORT}/docs" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "http://127.0.0.1:${BACKEND_PORT}/docs" >/dev/null 2>&1

cd "$PROJECT_ROOT/frontend"
rm -rf dist
npm run build >> "$FRONTEND_LOG" 2>&1
npm run preview \
  >> "$FRONTEND_LOG" 2>&1 &
FRONTEND_PID=$!
echo "$FRONTEND_PID" > "$LOG_DIR/frontend.pid"

for _ in {1..40}; do
  curl -fsS "http://127.0.0.1:${FRONTEND_PORT}/" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "http://127.0.0.1:${FRONTEND_PORT}/" >/dev/null 2>&1

while true; do
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "[stock-dashboard] backend exited; restarting supervisor"
    exit 1
  fi
  if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
    echo "[stock-dashboard] frontend exited; restarting supervisor"
    exit 1
  fi
  sleep 5
done
