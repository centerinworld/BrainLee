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
PEAK_LOG="$LOG_DIR/peak_monitor.launchd.log"

mkdir -p "$LOG_DIR"

cleanup() {
  trap - INT TERM EXIT
  [ -n "${BACKEND_PID:-}" ] && kill -TERM "$BACKEND_PID" 2>/dev/null || true
  [ -n "${FRONTEND_PID:-}" ] && kill -TERM "$FRONTEND_PID" 2>/dev/null || true
  [ -n "${PEAK_PID:-}" ] && kill -TERM "$PEAK_PID" 2>/dev/null || true
  "$PROJECT_ROOT/stop.sh" --processes-only >/dev/null 2>&1 || true
}

trap cleanup INT TERM EXIT

"$PROJECT_ROOT/stop.sh" --processes-only

cd "$PROJECT_ROOT"
# 50MB 초과 시 기존 로그 교체 (rotate)
if [ -f "$BACKEND_LOG" ] && [ "$(stat -f%z "$BACKEND_LOG" 2>/dev/null || echo 0)" -gt 52428800 ]; then
  mv "$BACKEND_LOG" "${BACKEND_LOG%.log}.1.log"
fi
"$PROJECT_ROOT/venv/bin/uvicorn" main:app \
  --host 127.0.0.1 \
  --port "$BACKEND_PORT" \
  --workers 1 \
  --log-level warning \
  --no-access-log \
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

start_peak_monitor() {
  cd "$PROJECT_ROOT"
  "$PROJECT_ROOT/venv/bin/python" peak_monitor.py \
    >> "$PEAK_LOG" 2>&1 &
  PEAK_PID=$!
  # peak_monitor.py가 자체적으로 pid 파일을 관리하므로 여기서 쓰지 않음
  # (serve_foreground.sh가 먼저 쓰면 peak_monitor가 자기 자신을 중복으로 감지)
}

start_peak_monitor

while true; do
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "[stock-dashboard] backend exited; restarting supervisor"
    exit 1
  fi
  if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
    echo "[stock-dashboard] frontend exited; restarting supervisor"
    exit 1
  fi
  if ! kill -0 "$PEAK_PID" 2>/dev/null; then
    echo "[stock-dashboard] peak monitor exited; restarting it"
    # 백엔드/프론트엔드는 그대로 두고 peak_monitor만 재시작
    pkill -f "python peak_monitor.py" 2>/dev/null || true
    rm -f "$LOG_DIR/peak_monitor.pid"
    sleep 2
    start_peak_monitor
  fi
  sleep 5
done
