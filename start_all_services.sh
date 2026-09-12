#!/bin/bash
# ==============================================================================
# Project AGI Development & CEO Briefing Platform - All-In-One Unified Service Runner
# Location: /Volumes/Realtek_NVME/AI System
# ==============================================================================

set -e
BASE_DIR="/Volumes/Realtek_NVME/AI System"
PYTHON_BIN="$BASE_DIR/antigravity_workspace/venv/bin/python"

echo "=========================================================="
echo "🚀 [AI System] Project AGI Development 통합 서비스 가동 시작"
echo "📂 실행 경로: $BASE_DIR"
echo "=========================================================="

# 1. 기존 프로세스 정리 (8011, 5500, 8501)
echo "🧹 기존 포트 점유 프로세스 정리 중..."
kill -9 $(lsof -t -i:8011) 2>/dev/null || true
kill -9 $(lsof -t -i:5500) 2>/dev/null || true
kill -9 $(lsof -t -i:8501) 2>/dev/null || true
sleep 1

# 2. FastAPI Backend 가동 (Port 8011)
echo "🔹 [1/3] Backend API (Port 8011 - api.newsinfo.cloud) 시작..."
cd "$BASE_DIR/codex/ceo-briefing-platform/backend"
nohup "$PYTHON_BIN" -m uvicorn main:app --port 8011 --host 0.0.0.0 --log-level info > "$BASE_DIR/backend_8011.log" 2>&1 &
echo "   -> Backend PID: $!"

# 3. Frontend Web Server 가동 (Port 5500)
echo "🔹 [2/3] Frontend Console (Port 5500 - newsinfo.cloud) 시작..."
cd "$BASE_DIR/codex/ceo-briefing-platform/frontend"
nohup "$PYTHON_BIN" -m http.server 5500 --bind 127.0.0.1 > "$BASE_DIR/frontend_5500.log" 2>&1 &
echo "   -> Frontend PID: $!"

# 4. Streamlit HUD Dashboard 가동 (Port 8501)
echo "🔹 [3/3] Streamlit HUD 관제 보드 (Port 8501) 시작..."
cd "$BASE_DIR/antigravity_workspace"
nohup "$PYTHON_BIN" -m streamlit run dashboard/app.py --server.port 8501 --server.headless true --server.address 0.0.0.0 > "$BASE_DIR/streamlit_8501.log" 2>&1 &
echo "   -> Streamlit PID: $!"

sleep 2
echo "=========================================================="
echo "✅ 모든 AI 시스템 서비스가 외장 SSD에서 정상 가동 중입니다."
echo "   - AI 관제 센터: https://newsinfo.cloud/kai/ (또는 http://localhost:5500/kai/)"
echo "   - 관리자 콘솔:   https://newsinfo.cloud/ (또는 http://localhost:5500/)"
echo "   - API 서버:     https://api.newsinfo.cloud/ (또는 http://localhost:8011/)"
echo "   - Streamlit HUD: http://localhost:8501/"
echo "=========================================================="
