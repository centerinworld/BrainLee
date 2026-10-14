#!/bin/bash
# W5 재실행(2026-10-13) 드라이버 — 4개씩 묶어서 순차 실행(macOS 기본 bash 3.2는 wait -n 미지원)
cd /Volumes/Realtek_NVME/stock_dashboard/runtime
set -a; . ./.env 2>/dev/null; set +a
export PYTHONPATH=runtime_pg_bootstrap:.
LOGDIR=/private/tmp/claude-501/-Volumes-Realtek-NVME-stock-dashboard/cf38852c-2800-4c02-b147-cb48c5d1d0fd/scratchpad/w5_20261013_logs
mkdir -p "$LOGDIR"

run_one() {
  which=$1; strat=$2
  if [ "$which" = "a" ]; then script=scripts/run_w5a_20261013.py; else script=scripts/run_w5b_20261013.py; fi
  echo "START $strat $(date)"
  venv/bin/python3 "$script" --strategy "$strat" >> "$LOGDIR/${which}_${strat}.log" 2>&1
  echo "END $strat rc=$? $(date)"
}

jobs_list="a:v_trend a:v1_value a:v2 a:v5 a:v10 a:v11 a:vbr a:minervini a:v12 a:sector_focus b:v8 b:deep_recovery b:extreme_dd_volume b:low_base_breakout b:megatrend b:recovery b:earnings_conviction b:earnings_supply_discovery b:moonshot_turnaround b:contract_momentum"

chunk=""
count=0
for item in $jobs_list; do
  chunk="$chunk $item"
  count=$((count+1))
  if [ "$count" -ge 4 ]; then
    for it in $chunk; do
      which=${it%%:*}; strat=${it#*:}
      run_one "$which" "$strat" &
    done
    wait
    chunk=""; count=0
  fi
done
if [ -n "$chunk" ]; then
  for it in $chunk; do
    which=${it%%:*}; strat=${it#*:}
    run_one "$which" "$strat" &
  done
  wait
fi
echo ALL_DONE
