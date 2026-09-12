#!/bin/bash
# ==============================================================================
# Project AGI Development — 통합 서비스 가동 스크립트
# 실행 환경: 외장 NVME SSD (/Volumes/Realtek_NVME/AI System)
# ==============================================================================

PROJECT_DIR="/Volumes/Realtek_NVME/AI System"
LOG_DIR="$PROJECT_DIR/logs"
PYTHON_BIN="$PROJECT_DIR/venv/bin/python3"
mkdir -p "$LOG_DIR"

if [ ! -f "$PYTHON_BIN" ]; then
    PYTHON_BIN="/Volumes/Realtek_NVME/stock_dashboard/venv/bin/python3"
fi

echo "=========================================================="
echo "🚀 [AI System] Project AGI Development 통합 서비스 가동 시작"
echo "📂 실행 경로: $PROJECT_DIR"
echo "🐍 파이썬 환경: $PYTHON_BIN"
echo "=========================================================="

# 1. 기존 프로세스 정리
echo "🧹 기존 포트 점유 프로세스 정리 중..."
lsof -ti :8011 | xargs kill -9 2>/dev/null
lsof -ti :5500 | xargs kill -9 2>/dev/null
lsof -ti :8501 | xargs kill -9 2>/dev/null
sleep 1

# 2. 백엔드 API 서버 가동 (Port 8011)
echo "🔹 [1/3] Backend API (Port 8011 - api.newsinfo.cloud) 시작..."
cd "$PROJECT_DIR/codex/ceo-briefing-platform/backend" || exit 1
nohup "$PYTHON_BIN" main.py > "$LOG_DIR/backend_8011.log" 2>&1 &
BACKEND_PID=$!
echo "   -> Backend PID: $BACKEND_PID"

# 3. 프론트엔드 정적 웹서버 가동 (Port 5500)
echo "🔹 [2/3] Frontend Console (Port 5500 - newsinfo.cloud) 시작..."
cd "$PROJECT_DIR/codex/ceo-briefing-platform/frontend" || exit 1
nohup "$PYTHON_BIN" -m http.server 5500 > "$LOG_DIR/frontend_5500.log" 2>&1 &
FRONTEND_PID=$!
echo "   -> Frontend PID: $FRONTEND_PID"

# 4. Streamlit HUD 관제 보드 가동 (Port 8501)
echo "🔹 [3/3] Streamlit HUD 관제 보드 (Port 8501) 시작..."
cd "$PROJECT_DIR/antigravity_workspace/dashboard" || exit 1
nohup "$PYTHON_BIN" -m streamlit run app.py --server.port 8501 --server.headless true --server.address 0.0.0.0 > "$LOG_DIR/streamlit_8501.log" 2>&1 &
STREAMLIT_PID=$!
echo "   -> Streamlit PID: $STREAMLIT_PID"

echo "=========================================================="
echo "✅ 모든 AI 시스템 서비스가 정상 가동 중입니다."
echo "   - AI 관제 센터: https://newsinfo.cloud/kai/ (또는 http://localhost:5500/kai/)"
echo "   - 관리자 콘솔:   https://newsinfo.cloud/ (또는 http://localhost:5500/)"
echo "   - API 서버:     https://api.newsinfo.cloud/ (또는 http://localhost:8011/)"
echo "   - Streamlit HUD: http://localhost:8501/"
echo "=========================================================="
