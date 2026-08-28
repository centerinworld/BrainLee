#!/bin/bash
# cleanup_dry_run.sh — 정리 대상 파일 목록 출력 (실제 삭제/이동 없음)
# 사용법: bash scripts/ops/cleanup_dry_run.sh

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

echo "======================================================"
echo "  CLEANUP DRY-RUN (아무것도 삭제/이동하지 않음)"
echo "  실행일: $(date)"
echo "======================================================"
echo ""

TOTAL_SIZE=0

# ── 1. .bak / .save 파일 ──────────────────────────────────
echo "[저위험] .bak/.save 파일:"
while IFS= read -r f; do
  sz=$(du -sh "$f" 2>/dev/null | cut -f1)
  echo "  MOVE: $f ($sz)"
done < <(find "$PROJECT_ROOT" -maxdepth 2 \
  \( -name "*.bak" -o -name "*.save" \) \
  -not -path "*/venv/*" -not -path "*/.git/*" 2>/dev/null)
echo ""

# ── 2. 빈 DB 파일 ──────────────────────────────────────────
echo "[저위험] 빈 DB 파일:"
for f in "$PROJECT_ROOT"/*.db; do
  [ -f "$f" ] && [ ! -s "$f" ] && echo "  DELETE: $f (0바이트)"
done
echo ""

# ── 3. 손상 백업 DB ────────────────────────────────────────
echo "[중위험] 외부 DB 백업 파일:"
find "$PROJECT_ROOT" -maxdepth 1 -name "*corrupted*backup*.db" 2>/dev/null | while read -r f; do
  sz=$(du -sh "$f" 2>/dev/null | cut -f1)
  echo "  ARCHIVE: $f ($sz) → archives/2026-05-16/corrupted_db/"
done
echo ""

# ── 4. venv_backup ─────────────────────────────────────────
if [ -d "$PROJECT_ROOT/venv_backup" ]; then
  sz=$(du -sh "$PROJECT_ROOT/venv_backup" 2>/dev/null | cut -f1)
  echo "[중위험] venv_backup: ARCHIVE ($sz) → archives/2026-05-16/venv_backup/"
else
  echo "[중위험] venv_backup: 없음"
fi
echo ""

# ── 5. 오래된 로그 (30일+) ────────────────────────────────
echo "[저위험] logs/ 내 30일+ 파일:"
find "$PROJECT_ROOT/logs" -mtime +30 -type f 2>/dev/null | while read -r f; do
  sz=$(du -sh "$f" 2>/dev/null | cut -f1)
  echo "  MOVE: $f ($sz)"
done
echo ""

# ── 6. DB backup 테이블 (30일+ 기준) ─────────────────────
echo "[중위험] DB backup 테이블 (30일+ 경과):"
TODAY=$(date +%Y%m%d)
CUTOFF=$(date -v-30d +%Y%m%d 2>/dev/null || date --date="30 days ago" +%Y%m%d 2>/dev/null)
sqlite3 "$PROJECT_ROOT/stock.db" \
  "SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE '%backup%' OR name LIKE '%bak%') ORDER BY name;" \
  2>/dev/null | while read -r tbl; do
  # 테이블명에서 날짜 추출 (YYYYMMDD 패턴)
  tbl_date=$(echo "$tbl" | grep -o '[0-9]\{8\}' | tail -1)
  if [ -n "$tbl_date" ] && [ "$tbl_date" -lt "$CUTOFF" ]; then
    echo "  EXPORT+DROP: $tbl (날짜: $tbl_date < $CUTOFF)"
  else
    echo "  KEEP: $tbl (30일 미만 또는 날짜 불명)"
  fi
done
echo ""

# ── 7. 임시/디버그 파일 후보 ──────────────────────────────
echo "[저위험] 임시/디버그 파일 후보 (루트):"
for f in \
  "$PROJECT_ROOT/debug_naver.py" \
  "$PROJECT_ROOT/find_naver_api.py" \
  "$PROJECT_ROOT/test_interp.py" \
  "$PROJECT_ROOT/test_krx_api.py" \
  "$PROJECT_ROOT/jinju_apt_tracker.py" \
  "$PROJECT_ROOT/signal_logic_v1_backup.py"; do
  [ -f "$f" ] && echo "  ARCHIVE: $(basename $f)"
done
echo ""

echo "======================================================"
echo "  DRY-RUN 완료. 실제 실행하려면:"
echo "  bash scripts/ops/archive_candidates.sh"
echo "======================================================"
