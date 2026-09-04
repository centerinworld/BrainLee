#!/bin/bash
# session_start.sh — UserPromptSubmit hook
# 매 프롬프트 제출 시 실행:
#   1) App.jsx 심볼릭 링크 보장 (워크트리 구버전 덮어쓰기 방지)
#   2) CLAUDE.md 지시사항 주입

INPUT=$(cat)

MAIN_APP="/Volumes/Realtek_NVME/stock_dashboard/runtime/frontend/src/App.jsx"
WORKTREE_BASE="/Volumes/Realtek_NVME/stock_dashboard/runtime/.claude/worktrees"

# ── 모든 워크트리의 App.jsx를 심볼릭 링크로 강제 교체 ──────────────────
for wt_app in "$WORKTREE_BASE"/*/frontend/src/App.jsx; do
    [ -e "$wt_app" ] || continue          # 파일/링크 없으면 스킵
    if [ ! -L "$wt_app" ]; then           # 링크가 아닌 실제 파일이면
        rm -f "$wt_app"
        ln -s "$MAIN_APP" "$wt_app"
    fi
done

cat <<'EOF'
{
  "systemMessage": "【필수 지시사항 — 매 응답마다 준수】\n1. /Volumes/Realtek_NVME/stock_dashboard/runtime/CLAUDE.md 는 이 프로젝트의 핵심 참조 문서입니다.\n2. 코드 수정/추가/삭제 작업을 완료한 후에는 반드시 CLAUDE.md를 업데이트하세요.\n3. 업데이트 대상: 새 파일 추가, API 엔드포인트 변경, DB 스키마 변경, 버그수정 내역, 알려진 이슈.\n4. 여러 작업이 있으면 마지막 작업 완료 후 한 번에 CLAUDE.md를 업데이트하세요.\n5. 파일을 열기 전에 항상 CLAUDE.md를 먼저 확인하여 불필요한 파일 읽기를 최소화하세요.\n6. ⛔ App.jsx는 반드시 /Volumes/Realtek_NVME/stock_dashboard/runtime/frontend/src/App.jsx (메인 경로)만 수정하세요. 워크트리 경로의 App.jsx는 이미 심볼릭 링크이므로 동일한 파일입니다."
}
EOF

exit 0
