#!/bin/zsh
# AGI-06 기업 보고서 매일 재생성(2026-10-14) — launchd com.stock-dashboard.agi-company-report-refresh.
# 숫자 데이터 수집(DART 00:20·FnGuide 04:00)과 재무무결성일일(06:20) 이후에 돈다.
cd /Volumes/Realtek_NVME/stock_dashboard/runtime || exit 1
export PYTHONPATH=runtime_pg_bootstrap:.
LOG=research_outputs/financial_rereview_20261002/agi_company_report_refresh.log
PY=venv/bin/python

LOCKD=research_outputs/financial_rereview_20261002/.agi_company_report_refresh.lock
if ! mkdir "$LOCKD" 2>/dev/null; then
  if [ -f "$LOCKD/pid" ] && kill -0 "$(cat $LOCKD/pid)" 2>/dev/null; then
    echo "$(date '+%F %T') ⏭ 이미 실행 중(pid $(cat $LOCKD/pid)) — 건너뜀" >> $LOG; exit 0
  fi
  rm -rf "$LOCKD"; mkdir "$LOCKD"
fi
echo $$ > "$LOCKD/pid"
trap 'rm -rf "$LOCKD"' EXIT

echo "$(date '+%F %T') ▶ agi_company_report_refresh" >> $LOG
$PY scripts/review/agi_company_report_refresh_20261014.py >> $LOG 2>&1
echo "$(date '+%F %T') ◀ rc=$?" >> $LOG
