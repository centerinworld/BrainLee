#!/bin/zsh
PROJECT_ROOT="$(cd "$(dirname "$0")"; pwd)"

echo "======================================================"
echo "  프로젝트 안티그래비티 시작"
echo "======================================================"

# ── 1. 포트 충돌 사전 해제 ──────────────────────────────
echo "포트 정리 중..."
lsof -ti :8000 | xargs kill -9 2>/dev/null
lsof -ti :5173 | xargs kill -9 2>/dev/null
sleep 1

# ── 2. 백엔드 서버 실행 ─────────────────────────────────
echo "백엔드 서버 시작..."
osascript -e "tell application \"Terminal\" to do script \"cd '$PROJECT_ROOT' && source venv/bin/activate && python3 run_server.py\""

# ── 3. 백엔드 준비 대기 (최대 30초) ────────────────────
echo "백엔드 준비 대기 중..."
READY=0
for i in $(seq 1 30); do
    if curl -s http://127.0.0.1:8000/ > /dev/null 2>&1; then
        READY=1
        echo "백엔드 준비 완료! (${i}초)"
        break
    fi
    sleep 1
done

if [ $READY -eq 0 ]; then
    echo "[경고] 백엔드 30초 내 응답 없음. 계속 진행합니다..."
fi

# ── 4. 데이터 수집기 실행 ───────────────────────────────
echo "데이터 수집기 시작..."
osascript -e "tell application \"Terminal\" to do script \"cd '$PROJECT_ROOT' && source venv/bin/activate && python3 data_collector.py\""

# ── 5. 프론트엔드 실행 ──────────────────────────────────
echo "프론트엔드 시작..."
osascript -e "tell application \"Terminal\" to do script \"cd '$PROJECT_ROOT/frontend' && npm run dev\""

# ── 6. 프론트엔드 준비 대기 후 브라우저 자동 열기 ───────
echo "브라우저 열기 대기 중..."
sleep 4
open http://localhost:5173

echo "======================================================"
echo "  모든 서비스 시작 완료!"
echo "  대시보드: http://localhost:5173"
echo "  API 문서: http://127.0.0.1:8000/docs"
echo "======================================================"
