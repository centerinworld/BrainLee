#!/bin/bash
# ==============================================================================
# Project AGI Development — 통합 서비스 가동 스크립트
# 실행 환경: 외장 NVME SSD (/Volumes/Realtek_NVME/AI System)
# ==============================================================================

PROJECT_DIR="/Volumes/Realtek_NVME/AI System"
LOG_DIR="$PROJECT_DIR/logs"
PYTHON_BIN="$PROJECT_DIR/venv/bin/python3"

# A09: 외장 SSD가 마운트되어 있지 않으면 아무것도 쓰지 않고 즉시 중단한다.
# (엉뚱한 로컬 경로에 새 로그/DB가 생기는 사고를 방지)
if [ ! -d "/Volumes/Realtek_NVME" ]; then
    echo "❌ 외장 SSD(/Volumes/Realtek_NVME)가 마운트되어 있지 않습니다. 중단합니다."
    exit 1
fi
if [ ! -d "$PROJECT_DIR" ]; then
    echo "❌ 프로젝트 경로($PROJECT_DIR)를 찾을 수 없습니다. 중단합니다."
    exit 1
fi

mkdir -p "$LOG_DIR"

if [ ! -f "$PYTHON_BIN" ]; then
    PYTHON_BIN="/Volumes/Realtek_NVME/stock_dashboard/venv/bin/python3"
fi
if [ ! -f "$PYTHON_BIN" ]; then
    echo "❌ 파이썬 실행 파일을 찾을 수 없습니다 ($PYTHON_BIN). 중단합니다."
    exit 1
fi

echo "=========================================================="
echo "🚀 [AI System] Project AGI Development 통합 서비스 가동 시작"
echo "📂 실행 경로: $PROJECT_DIR"
echo "🐍 파이썬 환경: $PYTHON_BIN"
echo "=========================================================="

# 포트가 실제로 비워질 때까지 대기(A09: kill 직후 바로 nohup하면 아직 점유 중인 상태에서
# 시작을 시도해 실패하거나, 예상치 못한 다른 프로세스가 그 사이 포트를 잡는 경쟁을 줄인다)
wait_port_free() {
    local port="$1"
    local tries=0
    while lsof -ti ":$port" > /dev/null 2>&1; do
        tries=$((tries + 1))
        if [ "$tries" -ge 10 ]; then
            echo "   ⚠️  포트 $port 가 10초 후에도 비워지지 않았습니다. 점유 중인 프로세스를 직접 확인하세요."
            return 1
        fi
        sleep 1
    done
    return 0
}

# 포트가 실제로 응답할 때까지 대기 (준비 상태 검증 - A09)
wait_port_ready() {
    local port="$1"
    local name="$2"
    local tries=0
    while ! lsof -ti ":$port" > /dev/null 2>&1; do
        tries=$((tries + 1))
        if [ "$tries" -ge 15 ]; then
            echo "   ❌ $name (포트 $port)가 15초 후에도 준비되지 않았습니다. 로그를 확인하세요."
            return 1
        fi
        sleep 1
    done
    echo "   ✅ $name (포트 $port) 준비 완료"
    return 0
}

# 1. 기존 프로세스 정리
echo "🧹 기존 포트 점유 프로세스 정리 중..."
for port in 8011 5500 8501; do
    lsof -ti ":$port" | xargs kill -9 2>/dev/null
done
for port in 8011 5500 8501; do
    wait_port_free "$port"
done

# 2. 백엔드 API 서버 가동 (Port 8011)
echo "🔹 [1/3] Backend API (Port 8011 - api.newsinfo.cloud) 시작..."
cd "$PROJECT_DIR/codex/ceo-briefing-platform/backend" || exit 1
nohup "$PYTHON_BIN" main.py > "$LOG_DIR/backend_8011.log" 2>&1 &
BACKEND_PID=$!
echo "   -> Backend PID: $BACKEND_PID"
wait_port_ready 8011 "Backend API"

# 3. 프론트엔드 정적 웹서버 가동 (Port 5500)
echo "🔹 [2/3] Frontend Console (Port 5500 - newsinfo.cloud) 시작..."
cd "$PROJECT_DIR/codex/ceo-briefing-platform/frontend" || exit 1
nohup "$PYTHON_BIN" -m http.server 5500 > "$LOG_DIR/frontend_5500.log" 2>&1 &
FRONTEND_PID=$!
echo "   -> Frontend PID: $FRONTEND_PID"
wait_port_ready 5500 "Frontend Console"

# 4. Streamlit HUD 관제 보드 가동 (Port 8501)
echo "🔹 [3/3] Streamlit HUD 관제 보드 (Port 8501) 시작..."
cd "$PROJECT_DIR/antigravity_workspace/dashboard" || exit 1
nohup "$PYTHON_BIN" -m streamlit run app.py --server.port 8501 --server.headless true --server.address 0.0.0.0 > "$LOG_DIR/streamlit_8501.log" 2>&1 &
STREAMLIT_PID=$!
echo "   -> Streamlit PID: $STREAMLIT_PID"
wait_port_ready 8501 "Streamlit HUD"

echo "=========================================================="
echo "✅ 모든 AI 시스템 서비스가 정상 가동 중입니다."
echo "   - AI 관제 센터: https://newsinfo.cloud/kai/ (또는 http://localhost:5500/kai/)"
echo "   - 관리자 콘솔:   https://newsinfo.cloud/ (또는 http://localhost:5500/)"
echo "   - API 서버:     https://api.newsinfo.cloud/ (또는 http://localhost:8011/)"
echo "   - Streamlit HUD: http://localhost:8501/"
echo "=========================================================="
