#!/bin/zsh
# Stop local dashboard processes and free the fixed development ports.
set -euo pipefail

PROJECT_ROOT="/Applications/stock_dashboard"
LOG_DIR="$PROJECT_ROOT/logs"
LABEL="com.stock-dashboard.local"
PROCESSES_ONLY=0

if [ "${1:-}" = "--processes-only" ]; then
  PROCESSES_ONLY=1
fi

if [ "$PROCESSES_ONLY" = "0" ]; then
  launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/${LABEL}.plist" >/dev/null 2>&1 || true
fi

stop_pid_file() {
  local name="$1"
  local file="$LOG_DIR/${name}.pid"
  if [ -f "$file" ]; then
    local pid
    pid="$(cat "$file" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      echo "[stock-dashboard] stopping $name pid $pid"
      kill -TERM "$pid" 2>/dev/null || true
    fi
    rm -f "$file"
  fi
}

mkdir -p "$LOG_DIR"

stop_pid_file backend
stop_pid_file frontend
stop_pid_file peak_monitor

# Catch servers started by older scripts or manual shell sessions.
pkill -f "/Applications/stock_dashboard/venv/bin/uvicorn main:app" 2>/dev/null || true
pkill -f "/Volumes/Realtek_NVME/stock_dashboard/venv/bin/uvicorn main:app" 2>/dev/null || true
pkill -f "uvicorn main:app" 2>/dev/null || true
pkill -f "/Applications/stock_dashboard/venv/bin/python peak_monitor.py" 2>/dev/null || true
pkill -f "/Volumes/Realtek_NVME/stock_dashboard/venv/bin/python peak_monitor.py" 2>/dev/null || true
pkill -f "python peak_monitor.py" 2>/dev/null || true
pkill -f "vite --host" 2>/dev/null || true
pkill -f "npm run dev" 2>/dev/null || true

sleep 1

for port in 8000 5173 5174 5175 5176; do
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    echo "[stock-dashboard] freeing port $port: $pids"
    kill -TERM $pids 2>/dev/null || true
  fi
done

sleep 1

for port in 8000 5173 5174 5175 5176; do
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    kill -KILL $pids 2>/dev/null || true
  fi
done

echo "[stock-dashboard] stopped"
