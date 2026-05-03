#!/bin/zsh
set -euo pipefail

ROOT="/Applications/stock_dashboard"
PY="$ROOT/venv/bin/python"
LOG="$ROOT/watchdog.log"
MARKER_DIR="/tmp/stock_dashboard_watchdog"
LOCK_DIR="$MARKER_DIR/.lock"
BACKEND_LOG="$ROOT/server.log"
FRONTEND_LOG="$ROOT/frontend/vite.log"

mkdir -p "$MARKER_DIR"

# 중복 실행 방지: 이전 주기가 아직 돌고 있으면 즉시 종료
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  # 고착 락(2시간 초과) 자동 해제
  now_ts=$(date +%s)
  lock_mtime=$(stat -f %m "$LOCK_DIR" 2>/dev/null || echo "0")
  if (( now_ts - lock_mtime > 7200 )); then
    rmdir "$LOCK_DIR" 2>/dev/null || true
    if ! mkdir "$LOCK_DIR" 2>/dev/null; then
      exit 0
    fi
  else
    exit 0
  fi
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

ts() {
  date '+%Y-%m-%d %H:%M:%S'
}

log() {
  echo "[$(ts)] $*" >> "$LOG"
}

is_backend_up() {
  # 프로세스명 매칭은 환경에 따라 오탐이 있어 "포트/응답" 기준으로 판정
  if lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
    return 0
  fi
  curl -fsS --max-time 2 "http://127.0.0.1:8000/" >/dev/null 2>&1
}

is_frontend_up() {
  # vite 프로세스명 대신 포트 우선 점검 (재시작 루프 방지)
  if lsof -nP -iTCP:5173 -sTCP:LISTEN >/dev/null 2>&1; then
    return 0
  fi
  curl -fsS --max-time 2 "http://127.0.0.1:5173/" >/dev/null 2>&1
}

restart_backend() {
  log "restart backend"
  pkill -f "uvicorn main:app" >/dev/null 2>&1 || true
  nohup "$ROOT/venv/bin/uvicorn" main:app --host 127.0.0.1 --port 8000 > "$BACKEND_LOG" 2>&1 &
  sleep 2
}

restart_frontend() {
  log "restart frontend"
  pkill -f "vite --host" >/dev/null 2>&1 || true
  (
    cd "$ROOT/frontend" && nohup npm run dev > "$FRONTEND_LOG" 2>&1 &
  )
  sleep 2
}

inc_fail_and_get() {
  local key="$1"
  local f="$MARKER_DIR/fail_${key}.cnt"
  local n=0
  if [[ -f "$f" ]]; then
    n=$(cat "$f" 2>/dev/null || echo "0")
  fi
  n=$((n+1))
  echo "$n" > "$f"
  echo "$n"
}

reset_fail() {
  local key="$1"
  local f="$MARKER_DIR/fail_${key}.cnt"
  echo "0" > "$f"
}

run_if_due() {
  local key="$1"
  local interval_sec="$2"
  shift 2
  local marker="$MARKER_DIR/${key}.ts"
  local now last=0
  now=$(date +%s)
  if [[ -f "$marker" ]]; then
    last=$(cat "$marker" 2>/dev/null || echo "0")
  fi
  if (( now - last < interval_sec )); then
    return 0
  fi
  log "run($key) start"
  if "$@"; then
    echo "$now" > "$marker"
    log "run($key) ok"
  else
    log "run($key) fail"
  fi
}

cd "$ROOT"

if is_backend_up; then
  reset_fail "backend"
else
  fb=$(inc_fail_and_get "backend")
  log "backend fail count=$fb"
  # 연속 2회 실패 시에만 재시작 (일시 응답지연 오탐 방지)
  if (( fb >= 2 )); then
    restart_backend
  fi
fi

if is_frontend_up; then
  reset_fail "frontend"
else
  ff=$(inc_fail_and_get "frontend")
  log "frontend fail count=$ff"
  if (( ff >= 2 )); then
    restart_frontend
  fi
fi

log "watchdog cycle done"
