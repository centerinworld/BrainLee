#!/bin/zsh
set -u

ROOT="/Volumes/Realtek_NVME/stock_dashboard/runtime"
PYTHON="$ROOT/venv/bin/python"
LOG_DIR="$ROOT/ETF_check/logs"
LOCK_DIR="$ROOT/ETF_check/run/daily_pipeline.lock"
TARGET_DATE="${1:-}"

if [[ -z "$TARGET_DATE" ]]; then
  if (( 10#$(date '+%H') < 12 )); then
    TARGET_DATE="$(date -v-1d '+%Y%m%d')"
  else
    TARGET_DATE="$(date '+%Y%m%d')"
  fi
fi

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
run_stage issuer_fallback "$PYTHON" ETF_check/issuer_pdf_fallback_v2.py --date "$TARGET_DATE" || failed=1
run_stage scale "$PYTHON" ETF_check/etf_scale_collector.py --date "$TARGET_DATE" || failed=1
run_stage etfcheck_sample "$PYTHON" ETF_check/etfcheck_k_sample_collector.py --date "$TARGET_DATE" || failed=1
run_stage full_pdf_audit "$PYTHON" ETF_check/full_pdf_audit.py || failed=1
run_stage rebalance_audit "$PYTHON" ETF_check/daily_rebalance_audit_v5.py --date "$TARGET_DATE" || failed=1
run_stage parity "$PYTHON" ETF_check/etf_parity_cutover_v2.py --date "$TARGET_DATE" || failed=1
run_stage postcondition "$PYTHON" ETF_check/verify_daily_pipeline.py --date "$TARGET_DATE" || failed=1

echo "[$(date '+%F %T')] END base_date=$TARGET_DATE exit=$failed"
exit $failed
