#!/bin/zsh
set -u

ROOT="/Volumes/Realtek_NVME/stock_dashboard/runtime"
PYTHON="$ROOT/venv/bin/python"
LOG_DIR="$ROOT/ETF_check/logs"
LOCK_DIR="$ROOT/ETF_check/run/daily_pipeline.lock"
TARGET_DATE="${1:-}"
ENABLE_ETFCHECK_VALIDATION="${ENABLE_ETFCHECK_VALIDATION:-0}"
export ENABLE_ETFCHECK_VALIDATION

if [[ -z "$TARGET_DATE" ]]; then
  if (( 10#$(date '+%H') < 12 )); then
    TARGET_DATE="$(date -v-1d '+%Y%m%d')"
  else
    TARGET_DATE="$(date '+%Y%m%d')"
  fi
fi

# Resolve weekends and exchange holidays once so every stage uses one KRX date.
TARGET_DATE="$(cd "$ROOT" && "$PYTHON" -c '
import sys
from datetime import datetime, timedelta
from trading_calendar import is_kr_trading_day
d = datetime.strptime(sys.argv[1], "%Y%m%d").date()
while not is_kr_trading_day(d):
    d -= timedelta(days=1)
print(d.strftime("%Y%m%d"))
' "$TARGET_DATE")"

mkdir -p "$LOG_DIR" "$ROOT/ETF_check/run"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "$(date '+%F %T') ETF daily pipeline already running" >> "$LOG_DIR/daily_pipeline.log"
  exit 0
fi
trap 'rmdir "$LOCK_DIR"' EXIT INT TERM

exec >> "$LOG_DIR/daily_pipeline.log" 2>&1
echo "[$(date '+%F %T')] START base_date=$TARGET_DATE"
cd "$ROOT" || exit 1
export PYTHONPATH="$ROOT/runtime_pg_bootstrap:$ROOT/ETF_check"

# A holiday or retry schedule may resolve to an already completed trading day.
# Do not repeat 1,000+ KRX/KIS requests when that snapshot is already published.
if [[ "$(sqlite3 "$ROOT/ETF_check/etf_check.db" "SELECT COUNT(*) FROM etf_direct_stock_publication WHERE base_date='$TARGET_DATE' AND status='published';")" == "1" ]] \
  && "$PYTHON" ETF_check/verify_daily_pipeline.py --date "$TARGET_DATE"; then
  echo "[$(date '+%F %T')] SKIP base_date=$TARGET_DATE already verified and published"
  echo "[$(date '+%F %T')] END base_date=$TARGET_DATE exit=0"
  exit 0
fi

run_stage() {
  local name="$1"
  shift
  echo "[$(date '+%F %T')] STAGE_START $name"
  "$@"
  local code=$?
  echo "[$(date '+%F %T')] STAGE_END $name exit=$code"
  return $code
}

failed=0
run_stage full_pdf "$PYTHON" ETF_check/full_pdf_collector_v7.py --date "$TARGET_DATE" || failed=1
# 2026-09-29: 상장폐지(추정) ETF가 KIS 마스터파일에 계속 남아 빈 PDF를 내면서 all-or-nothing
# 판정을 영구적으로 막던 문제 수정 — 매일 이 자리에서 자동으로 탐지·제외(및 부활 시 자동 복구).
# 이 단계 자체가 실패해도(신규 DB 등) 파이프라인 전체를 막지 않도록 failed에 반영하지 않는다.
run_stage delisting_watch "$PYTHON" ETF_check/etf_delisting_watch.py
run_stage issuer_fallback "$PYTHON" ETF_check/issuer_pdf_fallback_v2.py --date "$TARGET_DATE" || failed=1
run_stage scale "$PYTHON" ETF_check/etf_scale_collector.py --date "$TARGET_DATE" || failed=1
run_stage full_pdf_audit "$PYTHON" ETF_check/full_pdf_audit.py || failed=1
run_stage rebalance_audit "$PYTHON" ETF_check/daily_rebalance_audit_v5.py --date "$TARGET_DATE" || failed=1
if [[ "$ENABLE_ETFCHECK_VALIDATION" == "1" ]]; then
  run_stage etfcheck_sample "$PYTHON" ETF_check/etfcheck_k_sample_collector.py --date "$TARGET_DATE" || failed=1
  run_stage parity "$PYTHON" ETF_check/etf_parity_cutover_v2.py --date "$TARGET_DATE" || failed=1
else
  echo "[$(date '+%F %T')] STAGE_SKIP etfcheck_sample/parity external ETF Check disabled"
fi
run_stage postcondition "$PYTHON" ETF_check/verify_daily_pipeline.py --date "$TARGET_DATE" || failed=1

echo "[$(date '+%F %T')] END base_date=$TARGET_DATE exit=$failed"
exit $failed
