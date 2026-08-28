#!/usr/bin/env bash
set -u

ROOT="/Applications/stock_dashboard"
PY="$ROOT/venv/bin/python"
RUN_DIR="$ROOT/run/dart_missing_business_retry_20260621"
DONE_FILE="$RUN_DIR/.done"
WAIT_UNTIL_FILE="$RUN_DIR/.wait_until_epoch"

mkdir -p "$RUN_DIR"
cd "$ROOT" || exit 1

if [ -f "$DONE_FILE" ]; then
  echo "DART missing business retry already completed at $(cat "$DONE_FILE")"
  exit 0
fi

if [ -f "$WAIT_UNTIL_FILE" ]; then
  now_epoch="$(date +%s)"
  wait_until_epoch="$(cat "$WAIT_UNTIL_FILE" 2>/dev/null || echo 0)"
  if [ "$now_epoch" -lt "$wait_until_epoch" ]; then
    echo "DART API quota wait is active until $(date -r "$wait_until_epoch")"
    exit 0
  fi
  rm -f "$WAIT_UNTIL_FILE"
fi

if launchctl print "gui/$(id -u)/com.stock-dashboard.dart2020backfill" 2>/dev/null | grep -q "state = running"; then
  echo "DART 2020 backfill is still running at $(date); retry will wait."
  exit 0
fi

echo "DART missing business retry started at $(date)" | tee -a "$RUN_DIR/summary.log"

run_step() {
  local name="$1"
  shift
  local log="$RUN_DIR/${name}.log"
  echo "[$(date)] START $name: $*" | tee -a "$RUN_DIR/summary.log"
  "$@" > "$log" 2>&1
  local status=$?
  echo "[$(date)] END $name status=$status" | tee -a "$RUN_DIR/summary.log"
  if [ "$status" -ne 0 ]; then
    echo "[$(date)] WARN $name failed; continuing" | tee -a "$RUN_DIR/summary.log"
  fi
}

# 수주잔고는 이미 일부 수집됐지만 후보 공시 전체를 다시 훑어 누락분을 보강한다.
run_step "01_backlog_2020_2026_retry" \
  "$PY" -m collectors.dart_backlog_collector \
  --year-from 2020 --year-to 2026 --limit 100000

# corpCode.xml 누락 및 좁은 대상 조건으로 실패/누락됐던 매입재료비를 재시도한다.
run_step "02_material_purchase_2020_2026_retry" \
  "$PY" -m collectors.dart_material_purchase_collector \
  --years 2020 2021 2022 2023 2024 2025 2026 --limit 10000

# 임직원/판관비/매출채권 보강. 스크립트 내부 progress 파일로 이어받기.
run_step "03_employee_sga_ar_2020_2026_retry" \
  "$PY" scripts/collect_dart_ch_extra.py --limit 10000

run_step "04_final_page_data_audit" \
  "$PY" scripts/audit_all_page_data_quality.py

echo "DART missing business retry finished at $(date)" | tee -a "$RUN_DIR/summary.log"
date > "$DONE_FILE"
