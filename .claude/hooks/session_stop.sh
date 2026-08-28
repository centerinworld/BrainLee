#!/bin/bash
# session_stop.sh — Stop hook
# 작업 완료 후 CLAUDE.md 업데이트 여부를 체크하는 리마인더

INPUT=$(cat)

# 현재 시간 기록 (로그용)
echo "$(date '+%Y-%m-%d %H:%M:%S') — session stop" >> /Applications/stock_dashboard/.claude/session.log

cat <<'EOF'
{
  "continue": true,
  "suppressOutput": false
}
EOF

exit 0
