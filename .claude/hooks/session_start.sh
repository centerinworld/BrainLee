#!/bin/bash
# session_start.sh — UserPromptSubmit hook
# 매 프롬프트 제출 시 CLAUDE.md 읽기 및 업데이트 의무를 시스템 메시지로 주입

INPUT=$(cat)

cat <<'EOF'
{
  "systemMessage": "【필수 지시사항 — 매 응답마다 준수】\n1. /Applications/stock_dashboard/CLAUDE.md 는 이 프로젝트의 핵심 참조 문서입니다.\n2. 코드 수정/추가/삭제 작업을 완료한 후에는 반드시 CLAUDE.md를 업데이트하세요.\n3. 업데이트 대상: 새 파일 추가, API 엔드포인트 변경, DB 스키마 변경, 버그수정 내역, 알려진 이슈.\n4. 여러 작업이 있으면 마지막 작업 완료 후 한 번에 CLAUDE.md를 업데이트하세요.\n5. 파일을 열기 전에 항상 CLAUDE.md를 먼저 확인하여 불필요한 파일 읽기를 최소화하세요."
}
EOF

exit 0
