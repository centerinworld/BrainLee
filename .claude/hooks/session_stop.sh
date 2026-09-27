#!/bin/bash
# session_stop.sh — Stop hook
# 작업 완료 후 CLAUDE.md 업데이트 여부를 체크하는 리마인더

INPUT=$(cat)

# 현재 시간 기록 (로그용)
echo "$(date '+%Y-%m-%d %H:%M:%S') — session stop" >> /Volumes/Realtek_NVME/stock_dashboard/runtime/.claude/session.log

# 2026-09-27: 응답이 끝날 때마다 docs/SYSTEM_MAP.md(AI용 압축 시스템 지도)를 재생성 — 소스 fingerprint가 같으면 즉시 종료(비용 거의 없음)
( cd /Volumes/Realtek_NVME/stock_dashboard/runtime && set -a && . ./.env 2>/dev/null; set +a; \
  PYTHONPATH=runtime_pg_bootstrap:. venv/bin/python scripts/ops/gen_system_map_doc.py >> .claude/session.log 2>&1 ) &

cat <<'EOF'
{
  "continue": true,
  "suppressOutput": false
}
EOF

exit 0
