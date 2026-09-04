#!/usr/bin/env bash
set -euo pipefail

ROOT="/Volumes/Realtek_NVME/stock_dashboard/runtime"
cd "$ROOT"
TS="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="$ROOT/scratch"
PIPE_LOG="$LOG_DIR/resume_quarterly_pipeline_${TS}.log"
SUMMARY_TXT="$LOG_DIR/resume_quarterly_pipeline_${TS}_summary.txt"

log(){ echo "[$(date '+%F %T')] $*" | tee -a "$PIPE_LOG"; }

count_open(){
  sqlite3 "$ROOT/stock.db" "SELECT COALESCE(SUM(status='OPEN'),0) FROM fin_quarterly_validation_flags WHERE check_type='QUARTERLY_4WAY';"
}

count_ambig(){
  sqlite3 "$ROOT/stock.db" "SELECT COALESCE(SUM(status='AMBIGUOUS'),0) FROM fin_quarterly_validation_flags;"
}

log "=== resume_quarterly_pipeline start ==="
OPEN_BEFORE="$(count_open)"
AMBIG_BEFORE="$(count_ambig)"
log "before: QUARTERLY_4WAY OPEN=${OPEN_BEFORE}, ALL AMBIGUOUS=${AMBIG_BEFORE}"

# 1) 2016~2018 annual backfill (resume)
if [ -f "$ROOT/scratch/collect_dart_annual_2016_2018.py" ]; then
  log "run annual 2016~2018 collector (resume)"
  python3 "$ROOT/scratch/collect_dart_annual_2016_2018.py" >> "$PIPE_LOG" 2>&1 || true
fi

# 2) smallcap quarterly (resume)
if [ -f "$ROOT/scratch/collect_quarterly_smallcap.py" ]; then
  log "run smallcap quarterly collector (resume)"
  python3 "$ROOT/scratch/collect_quarterly_smallcap.py" >> "$PIPE_LOG" 2>&1 || true
fi

# 3) 2019~2021 quarterly (resume)
if [ -f "$ROOT/scratch/collect_quarterly_2019_2021.py" ]; then
  log "run 2019~2021 quarterly collector (resume)"
  python3 "$ROOT/scratch/collect_quarterly_2019_2021.py" >> "$PIPE_LOG" 2>&1 || true
fi

# 4) validation full pipeline
log "run daily validation pipeline"
bash "$ROOT/scratch/run_daily_validation.sh" >> "$PIPE_LOG" 2>&1

OPEN_AFTER="$(count_open)"
AMBIG_AFTER="$(count_ambig)"
log "after: QUARTERLY_4WAY OPEN=${OPEN_AFTER}, ALL AMBIGUOUS=${AMBIG_AFTER}"

{
  echo "timestamp=${TS}"
  echo "open_before=${OPEN_BEFORE}"
  echo "open_after=${OPEN_AFTER}"
  echo "open_delta=$((OPEN_AFTER-OPEN_BEFORE))"
  echo "ambiguous_before=${AMBIG_BEFORE}"
  echo "ambiguous_after=${AMBIG_AFTER}"
  echo "ambiguous_delta=$((AMBIG_AFTER-AMBIG_BEFORE))"
  echo ""
  sqlite3 "$ROOT/stock.db" "SELECT check_type||'|'||COUNT(*)||'|'||SUM(status='OPEN')||'|'||SUM(status='AMBIGUOUS')||'|'||SUM(status='CONFIRMED')||'|'||SUM(status='CLOSE_MATCH')||'|'||SUM(status='SELF_CONSISTENT')||'|'||SUM(status='STRUCTURAL') FROM fin_quarterly_validation_flags GROUP BY check_type ORDER BY check_type;"
} > "$SUMMARY_TXT"

log "summary: $SUMMARY_TXT"

# ── 자동 QA 로그 ─────────────────────────────────────────────────────────────
QA_DIR="$LOG_DIR/qa_logs"
python3 "$ROOT/scripts/ops/qa_report.py" --out-dir "$QA_DIR" | tee -a "$PIPE_LOG"
[ $? -eq 0 ] && log "✅ QA ALL PASS" || log "⚠️  QA FAIL — $QA_DIR/latest.txt 확인"

# ── 데이터 무결성 검사 ────────────────────────────────────────────────────────
python3 "$ROOT/scripts/ops/data_integrity_check.py" --out-dir "$QA_DIR" 2>&1 | tee -a "$PIPE_LOG"
[ $? -eq 0 ] && log "✅ INTEGRITY ALL PASS" || log "⚠️  INTEGRITY 이슈 — $QA_DIR/latest_integrity.txt 확인"

log "=== resume_quarterly_pipeline end ==="
