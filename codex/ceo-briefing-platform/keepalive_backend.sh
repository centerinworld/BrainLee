#!/bin/bash
BACKEND_DIR="/Users/brainlee/Downloads/codex/ceo-briefing-platform/backend"
FRONTEND_DIR="/Users/brainlee/Downloads/codex/ceo-briefing-platform/frontend"
PYTHON="/Applications/stock_dashboard/venv/bin/python"
LOG="/tmp/ceo_briefing.log"
BACKEND_PORT=8011
FRONTEND_PORT=5500

# 프론트엔드 HTTP 서버 유지 (포트 5500, Cloudflare newsinfo.cloud 원본)
if ! lsof -ti:$FRONTEND_PORT > /dev/null 2>&1; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 프론트엔드 서버 시작 (포트 $FRONTEND_PORT)..." >> "$LOG"
    cd "$FRONTEND_DIR"
    "$PYTHON" -m http.server $FRONTEND_PORT --bind 127.0.0.1 >> "$LOG" 2>&1 &
fi

# 백엔드 서버 유지 루프 (포트 8011)
while true; do
    # 프론트엔드 서버도 죽었으면 재시작
    if ! lsof -ti:$FRONTEND_PORT > /dev/null 2>&1; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 프론트엔드 서버 재시작..." >> "$LOG"
        cd "$FRONTEND_DIR"
        "$PYTHON" -m http.server $FRONTEND_PORT --bind 127.0.0.1 >> "$LOG" 2>&1 &
    fi

    if lsof -ti:$BACKEND_PORT > /dev/null 2>&1; then
        sleep 30
        continue
    fi
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 백엔드 서버 시작..." >> "$LOG"
    cd "$BACKEND_DIR"
    "$PYTHON" -m uvicorn main:app --port $BACKEND_PORT --host 0.0.0.0 --log-level info >> "$LOG" 2>&1
    EXIT_CODE=$?
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 백엔드 종료됨 (exit=$EXIT_CODE). 10초 후 재시작..." >> "$LOG"
    sleep 10
done
