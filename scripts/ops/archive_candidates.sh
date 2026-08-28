#!/bin/bash
# archive_candidates.sh — cleanup_dry_run.sh 검토 후 실제 이동 실행
# 사용법: bash scripts/ops/archive_candidates.sh
# 주의: 삭제는 없음. 모든 파일은 archives/2026-05-16/ 로 이동만 함.

set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ARCHIVE_DATE="2026-05-16"
ARCHIVE_BASE="$PROJECT_ROOT/archives/$ARCHIVE_DATE"

echo "======================================================"
echo "  ARCHIVE 실행 (이동만, 삭제 없음)"
echo "  대상: $ARCHIVE_BASE"
echo "  실행일: $(date)"
echo "======================================================"

# ── 1. .bak / .save 파일 → archives/misc/
MISC_DIR="$ARCHIVE_BASE/misc"
mkdir -p "$MISC_DIR"
find "$PROJECT_ROOT" -maxdepth 2 \
  \( -name "*.bak" -o -name "*.save" \) \
  -not -path "*/venv/*" -not -path "*/.git/*" 2>/dev/null | while read -r f; do
  echo "  MOVE: $f → $MISC_DIR/"
  mv "$f" "$MISC_DIR/"
done

# ── 2. 빈 DB 파일 삭제 (0바이트, 안전)
for f in "$PROJECT_ROOT"/*.db; do
  if [ -f "$f" ] && [ ! -s "$f" ]; then
    echo "  DELETE (0B): $f"
    rm -f "$f"
  fi
done

# ── 3. 손상 백업 DB → archives/corrupted_db/
CORRUPT_DIR="$ARCHIVE_BASE/corrupted_db"
mkdir -p "$CORRUPT_DIR"
find "$PROJECT_ROOT" -maxdepth 1 -name "*corrupted*backup*.db" 2>/dev/null | while read -r f; do
  echo "  MOVE: $(basename $f) → $CORRUPT_DIR/"
  mv "$f" "$CORRUPT_DIR/"
done

# ── 4. venv_backup → archives/venv_backup/
if [ -d "$PROJECT_ROOT/venv_backup" ]; then
  VENV_DIR="$ARCHIVE_BASE/venv_backup"
  mkdir -p "$ARCHIVE_BASE"
  echo "  MOVE: venv_backup → $VENV_DIR"
  mv "$PROJECT_ROOT/venv_backup" "$VENV_DIR"
fi

# ── 5. 임시/디버그 파일 → archives/temp_scripts/
TEMP_DIR="$ARCHIVE_BASE/temp_scripts"
mkdir -p "$TEMP_DIR"
for f in \
  "$PROJECT_ROOT/debug_naver.py" \
  "$PROJECT_ROOT/find_naver_api.py" \
  "$PROJECT_ROOT/test_interp.py" \
  "$PROJECT_ROOT/test_krx_api.py" \
  "$PROJECT_ROOT/jinju_apt_tracker.py" \
  "$PROJECT_ROOT/signal_logic_v1_backup.py"; do
  if [ -f "$f" ]; then
    echo "  MOVE: $(basename $f) → $TEMP_DIR/"
    mv "$f" "$TEMP_DIR/"
  fi
done

# ── 6. 오래된 로그 (30일+) → archives/old_logs/
OLD_LOG_DIR="$ARCHIVE_BASE/old_logs"
mkdir -p "$OLD_LOG_DIR"
find "$PROJECT_ROOT/logs" -mtime +30 -type f 2>/dev/null | while read -r f; do
  echo "  MOVE: $(basename $f) → $OLD_LOG_DIR/"
  mv "$f" "$OLD_LOG_DIR/"
done

echo ""
echo "======================================================"
echo "  완료. DB backup 테이블 정리는 별도 승인 후:"
echo "  bash scripts/ops/export_drop_backup_tables.sh"
echo "======================================================"
