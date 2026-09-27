#!/bin/bash
# ============================================================
# Project AGI — 통합 동기화 스크립트
# 2026-09-27: AI System이 유일한 실행본이다. 과거의 "AI System → ~/Downloads/codex"
# 미러링은 폐기했다(LaunchAgent가 AI System을 직접 서빙). 이제는 stock_dashboard에서
# 만들어지는 파일만 AI System으로 가져온다.
# 실행: bash sync_unified.sh
# ============================================================

AI_BACKEND="/Volumes/Realtek_NVME/AI System/codex/ceo-briefing-platform/backend"
STOCK_DIR="/Volumes/Realtek_NVME/stock_dashboard"

echo "🔄 [sync] Project AGI 통합 동기화 시작 ($(date '+%Y-%m-%d %H:%M:%S'))"

# 1. session_quota_status.json 동기화 (stock_dashboard → AI System)
if [ -f "$STOCK_DIR/session_quota_status.json" ]; then
    cp -f "$STOCK_DIR/session_quota_status.json" "$AI_BACKEND/session_quota_status.json" && echo "  [OK] session_quota_status.json → AI System"
fi

# 2. gemini_gems_worker.py 동기화 (stock_dashboard → AI System)
if [ -f "$STOCK_DIR/gemini_gems_worker.py" ]; then
    cp -f "$STOCK_DIR/gemini_gems_worker.py" "$AI_BACKEND/gemini_gems_worker.py" && echo "  [OK] gemini_gems_worker.py → AI System"
fi

echo "✅ [sync] 동기화 완료 ($(date '+%Y-%m-%d %H:%M:%S'))"
