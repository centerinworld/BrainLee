#!/usr/bin/env bash
set -u

ROOT="/Volumes/Realtek_NVME/stock_dashboard/runtime"
PY="$ROOT/venv/bin/python"
RUN_ID="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="$ROOT/run/dart_2020_backfill_$RUN_ID"
YEARS_LIST="2020 2021 2022 2023 2024 2025 2026"
YEARS_CSV="2020,2021,2022,2023,2024,2025,2026"

mkdir -p "$LOG_DIR"
cd "$ROOT" || exit 1

echo "DART 2020 backfill started at $(date)" | tee "$LOG_DIR/summary.log"
echo "Log dir: $LOG_DIR" | tee -a "$LOG_DIR/summary.log"

run_step() {
  local name="$1"
  shift
  local log="$LOG_DIR/${name}.log"
  echo "[$(date)] START $name: $*" | tee -a "$LOG_DIR/summary.log"
  "$@" > "$log" 2>&1
  local status=$?
  echo "[$(date)] END $name status=$status" | tee -a "$LOG_DIR/summary.log"
  if [ "$status" -ne 0 ]; then
    echo "[$(date)] WARN $name failed; continuing next step" | tee -a "$LOG_DIR/summary.log"
  fi
}

# 2020~2026 = current 2026 inclusive, seven fiscal years.
run_step "01_financial_2020_2026" "$PY" collect_dart_financial_batch.py --years 7
run_step "02_cashflow_2020_2026_fill_missing" "$PY" collect_dart_cashflow_batch.py --years 7 --fill-missing
run_step "03_inventory_2020_2026" "$PY" scripts/collect_inventory_from_dart.py --year $YEARS_LIST --quarter 1 2 3 4 --min-cap 0
run_step "04_material_purchase_2020_2026" "$PY" -m collectors.dart_material_purchase_collector --years $YEARS_LIST --limit 10000
run_step "05_backlog_text_2020_2026" "$PY" -m collectors.dart_backlog_collector --year-from 2020 --year-to 2026
run_step "06_cost_quarterly_2020_2026" "$PY" -m collectors.dart_cost_collector --year-from 2020 --year-to 2026
run_step "07_segment_breakdown_2020_2026" "$PY" scripts/collect_dart_segment_breakdown.py --years "$YEARS_CSV" --limit 10000
run_step "08_ch_extra_top10000" "$PY" scripts/collect_dart_ch_extra.py --limit 10000
run_step "09_report_items_2020_2026" "$PY" scripts/collect_dart_report_items.py --years $YEARS_LIST --limit 10000

echo "DART 2020 backfill finished at $(date)" | tee -a "$LOG_DIR/summary.log"
