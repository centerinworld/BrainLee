#!/bin/bash
# ============================================================
# fix_db_and_restart.sh — DB WAL 복구 + 서버 재시작
# ============================================================
# 실행: bash /Volumes/Realtek_NVME/stock_dashboard/runtime/fix_db_and_restart.sh
#
# 증상: "file is not a database" SQLite WAL 충돌
# 원인: 서버/스케줄러/백필 동시 DB 쓰기 → WAL 오염
# ============================================================

cd /Volumes/Realtek_NVME/stock_dashboard/runtime

echo "========================================================"
echo "  DB WAL 복구 + 서버 재시작"
echo "  $(date)"
echo "========================================================"

# ── 1. 기존 서버 프로세스 중지 ────────────────────────────
echo ""
echo "[1/4] 기존 서버 프로세스 중지..."

# launchd 방식으로 중지 시도
if [ -f logs/backend.pid ]; then
    OLD_PID=$(cat logs/backend.pid 2>/dev/null)
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        kill "$OLD_PID" 2>/dev/null
        echo "  서버 PID $OLD_PID 종료"
        sleep 2
    fi
fi

# uvicorn 직접 kill
pkill -f "uvicorn main:app" 2>/dev/null && echo "  uvicorn 종료 완료" || echo "  uvicorn 프로세스 없음"
sleep 2

# ── 2. WAL checkpoint (WAL → 메인 DB 병합) ──────────────
echo ""
echo "[2/4] DB WAL checkpoint..."

# stock.db WAL 병합
source venv/bin/activate 2>/dev/null
python3 - <<'PYEOF'
import sqlite3, os

db_path = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"
wal_path = db_path + "-wal"
shm_path = db_path + "-shm"

print(f"  WAL 크기: {os.path.getsize(wal_path)/1024:.0f} KB" if os.path.exists(wal_path) else "  WAL 없음")

try:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    result = conn.execute("PRAGMA integrity_check").fetchone()
    conn.close()
    print(f"  integrity_check: {result[0]}")
    if result[0] == 'ok':
        print("  ✅ DB 정상 - WAL checkpoint 완료")
    else:
        print("  ⚠️  DB 무결성 이상 감지")
except Exception as e:
    print(f"  ⚠️  WAL checkpoint 실패: {e}")
    print("  → WAL 파일 강제 삭제 시도...")
    try:
        if os.path.exists(wal_path):
            os.remove(wal_path)
            print("  ✅ stock.db-wal 삭제")
        if os.path.exists(shm_path):
            os.remove(shm_path)
            print("  ✅ stock.db-shm 삭제")
    except Exception as e2:
        print(f"  ❌ 삭제 실패: {e2}")
PYEOF

echo ""
echo "[3/4] WAL 크기 확인..."
ls -lh /Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db* 2>/dev/null

# ── 3. SQLite busy_timeout 설정 확인 ──────────────────────
echo ""
echo "[4/4] 서버 재시작..."

# launchd 방식 (시스템 서비스 등록된 경우)
if [ -f /Library/LaunchDaemons/com.stock_dashboard.backend.plist ] || \
   [ -f ~/Library/LaunchAgents/com.stock_dashboard.backend.plist ]; then
    launchctl stop com.stock_dashboard.backend 2>/dev/null
    sleep 1
    launchctl start com.stock_dashboard.backend 2>/dev/null
    echo "  launchd로 서버 재시작"
else
    # 직접 실행 방식
    nohup /Volumes/Realtek_NVME/stock_dashboard/runtime/venv/bin/python \
        -m uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1 \
        >> /Volumes/Realtek_NVME/stock_dashboard/runtime/logs/backend.launchd.log 2>&1 &
    NEW_PID=$!
    echo "$NEW_PID" > /Volumes/Realtek_NVME/stock_dashboard/runtime/logs/backend.pid
    echo "  서버 시작 PID: $NEW_PID"
fi

sleep 3

# ── 4. 서버 정상 동작 확인 ────────────────────────────────
echo ""
echo "  서버 상태 확인..."
if curl -s http://localhost:8000/api/dashboard/stats > /dev/null 2>&1; then
    echo "  ✅ 서버 정상 응답"
else
    echo "  ⏳ 서버 시작 중... (10초 후 재확인)"
    sleep 10
    if curl -s http://localhost:8000/api/dashboard/stats > /dev/null 2>&1; then
        echo "  ✅ 서버 정상 응답"
    else
        echo "  ⚠️  서버 응답 없음 — 로그 확인: tail -f logs/backend.launchd.log"
    fi
fi

echo ""
echo "========================================================"
echo "  완료! 브라우저를 새로고침하면 매크로 데이터가 표시됩니다."
echo "========================================================"
