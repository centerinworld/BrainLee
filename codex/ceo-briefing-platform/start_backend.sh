#!/bin/bash
# 수동 실행용. 평소에는 LaunchAgent(com.ceo-briefing.backend)가 관리한다.
ROOT="/Volumes/Realtek_NVME/AI System/codex/ceo-briefing-platform"
cd "$ROOT/backend"
exec "$ROOT/backend/.venv/bin/python" \
    -m uvicorn main:app \
    --port 8011 \
    --host 127.0.0.1 \
    --log-level warning
