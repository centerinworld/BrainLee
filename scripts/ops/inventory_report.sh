#!/bin/bash
# inventory_report.sh — 프로젝트 인벤토리 리포트 생성
# 사용법: bash scripts/ops/inventory_report.sh [출력파일]

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${1:-$PROJECT_ROOT/scratch/inventory_$(date +%Y%m%d_%H%M).txt}"

echo "====== Stock Dashboard 인벤토리 리포트 ======" > "$OUT"
echo "생성일: $(date)" >> "$OUT"
echo "" >> "$OUT"

echo "## 1. 전체 파일/폴더 수" >> "$OUT"
find "$PROJECT_ROOT" \
  -not -path "*/venv/*" \
  -not -path "*/venv_backup/*" \
  -not -path "*/.git/*" \
  -not -path "*/node_modules/*" \
  | wc -l >> "$OUT"
echo "" >> "$OUT"

echo "## 2. 용량 상위 경로 (Top 20)" >> "$OUT"
du -sh "$PROJECT_ROOT"/* 2>/dev/null | sort -rh | head -20 >> "$OUT"
echo "" >> "$OUT"

echo "## 3. 루트 Python 파일 수 및 목록" >> "$OUT"
ls "$PROJECT_ROOT"/*.py 2>/dev/null | wc -l >> "$OUT"
ls "$PROJECT_ROOT"/*.py 2>/dev/null >> "$OUT"
echo "" >> "$OUT"

echo "## 4. .bak/.save/.old/.orig 파일" >> "$OUT"
find "$PROJECT_ROOT" -maxdepth 3 \
  \( -name "*.bak" -o -name "*.save" -o -name "*.old" -o -name "*.orig" \) \
  -not -path "*/venv/*" -not -path "*/.git/*" 2>/dev/null >> "$OUT"
echo "" >> "$OUT"

echo "## 5. DB backup 테이블 목록" >> "$OUT"
sqlite3 "$PROJECT_ROOT/stock.db" \
  "SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE '%backup%' OR name LIKE '%bak%') ORDER BY name;" \
  2>/dev/null >> "$OUT"
echo "" >> "$OUT"

echo "## 6. DB 테이블 용량 추정 (상위 15)" >> "$OUT"
sqlite3 "$PROJECT_ROOT/stock.db" \
  "SELECT name, printf('%.1f MB', SUM(payload)/1048576.0) size FROM dbstat GROUP BY name ORDER BY SUM(payload) DESC LIMIT 15;" \
  2>/dev/null >> "$OUT"
echo "" >> "$OUT"

echo "## 7. .sh/.command 파일 목록" >> "$OUT"
ls "$PROJECT_ROOT"/*.sh "$PROJECT_ROOT"/*.command 2>/dev/null >> "$OUT"
echo "" >> "$OUT"

echo "## 8. 오래된 파일 (30일+, 루트 *.py)" >> "$OUT"
find "$PROJECT_ROOT" -maxdepth 1 -name "*.py" -mtime +30 2>/dev/null >> "$OUT"
echo "" >> "$OUT"

echo "## 9. 빈 파일 (0바이트)" >> "$OUT"
find "$PROJECT_ROOT" -maxdepth 2 -empty -not -path "*/.git/*" -not -path "*/venv/*" 2>/dev/null >> "$OUT"
echo "" >> "$OUT"

echo "## 10. logs/ 현황" >> "$OUT"
du -sh "$PROJECT_ROOT/logs/" 2>/dev/null >> "$OUT"
ls -lht "$PROJECT_ROOT/logs/" 2>/dev/null | head -20 >> "$OUT"
echo "" >> "$OUT"

echo "## 11. .claude/worktrees 현황" >> "$OUT"
ls -la "$PROJECT_ROOT/.claude/worktrees/" 2>/dev/null >> "$OUT"
echo "" >> "$OUT"

echo "====== 리포트 완료: $OUT ======"
cat "$OUT"
