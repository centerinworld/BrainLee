#!/bin/bash
# fix_ports.sh — 포트 충돌 해결 스크립트
# 실행: bash fix_ports.sh

echo "=== 포트 점유 프로세스 종료 ==="

for PORT in 5173 5174 8000; do
  PIDS=$(lsof -ti :$PORT 2>/dev/null)
  if [ -n "$PIDS" ]; then
    echo "포트 $PORT 점유 PID: $PIDS → 종료"
    kill -TERM $PIDS 2>/dev/null
    sleep 0.5
    # SIGTERM으로 안 죽으면 SIGKILL
    kill -KILL $PIDS 2>/dev/null
  else
    echo "포트 $PORT — 사용 중 없음"
  fi
done

echo ""
echo "=== 완료. 이제 서버를 다시 실행하세요 ==="
echo "  백엔드: python3 run_server.py"
echo "  프론트: cd frontend && npm run dev"
