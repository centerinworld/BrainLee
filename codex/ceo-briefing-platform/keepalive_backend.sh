#!/bin/bash
# 수동 보조용 keepalive (2026-09-27 갱신).
# 평소 프론트 5500/백엔드 8011/터널은 LaunchAgent(com.ceo-briefing.frontend,
# .backend, .cloudflared)가 유지한다. LaunchAgent가 내려간 상태에서 임시로 띄울 때만 쓴다.
# 포트가 이미 사용 중이면 아무것도 시작하지 않으므로 LaunchAgent와 충돌하지 않는다.
# 실행 기준은 AI System 하나뿐이다 (~/Downloads/codex 사본은 폐기됨).
ROOT="/Volumes/Realtek_NVME/AI System/codex/ceo-briefing-platform"
BACKEND_DIR="$ROOT/backend"
FRONTEND_DIR="$ROOT/frontend"
PYTHON="$BACKEND_DIR/.venv/bin/python"
LOG="/tmp/ceo_briefing.log"
BACKEND_PORT=8011
FRONTEND_PORT=5500

start_frontend() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 프론트엔드 서버 시작 (포트 $FRONTEND_PORT)..." >> "$LOG"
    (cd "$FRONTEND_DIR" && exec "$PYTHON" -m http.server $FRONTEND_PORT --bind 127.0.0.1 >> "$LOG" 2>&1) &
}

while true; do
    if ! lsof -ti:$FRONTEND_PORT > /dev/null 2>&1; then
        start_frontend
    fi

    if lsof -ti:$BACKEND_PORT > /dev/null 2>&1; then
        sleep 30
        continue
    fi
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 백엔드 서버 시작..." >> "$LOG"
    cd "$BACKEND_DIR"
    "$PYTHON" -m uvicorn main:app --port $BACKEND_PORT --host 127.0.0.1 --log-level info >> "$LOG" 2>&1
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 백엔드 종료됨 (exit=$?). 10초 후 재시작..." >> "$LOG"
    sleep 10
done
