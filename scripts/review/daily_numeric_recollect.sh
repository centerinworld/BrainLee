#!/bin/zsh
# 숫자 데이터 원문 재수집 — 매일 한도 안에서 이어 받기(2026-10-03, docs/FINANCIAL_STATEMENTS.md §9)
# launchd com.stock-dashboard.numeric-recollect 가 호출한다(재부팅에도 유지).
#   dart    (00:20) : DART 키1·키3만 사용(키2=공시 전용 제외). 각 단계는 한도 소진 시 스스로 종료 → 다음 날 이어서.
#   fnguide (04:00) : FnGuide wcomp 원문 저장(연결·별도·연간·분기). 일 1,500 공유 한도 중 기존 스윕(03:15) 이후 잔여 사용.
# 수집만 한다(운영 테이블 미변경). 적용은 사람이 dry-run 확인 후 별도 실행.
cd /Volumes/Realtek_NVME/stock_dashboard/runtime || exit 1
export PYTHONPATH=runtime_pg_bootstrap:.
LOG=research_outputs/financial_rereview_20261002/daily_numeric_recollect.log
PY=venv/bin/python
R=scripts/review
step() { echo "$(date '+%F %T') ▶ $*" >> $LOG; "$@" >> $LOG 2>&1; echo "$(date '+%F %T') ◀ rc=$?" >> $LOG; }

case "$1" in
  dart)
    step $PY $R/fetch_dart_cashflow_20261002.py --repair-parent              # 2023+ 지배주주 보완
    step $PY $R/fetch_dart_cashflow_20261002.py --prev-only                  # 2023+ 재작성값(전기 칸)
    step $PY $R/fetch_xbrl_depreciation_20261003.py --exit-on-quota          # 감가상각 XBRL
    step $PY $R/fetch_dart_cashflow_20261002.py --years 2016-2022            # 2016~2022 본 수집
    step $PY $R/fetch_dart_cashflow_20261002.py --years 2016-2022 --repair-parent
    step $PY $R/fetch_dart_cashflow_20261002.py --years 2016-2022 --prev-only
    ;;
  fnguide)
    step $PY $R/fetch_fnguide_raw_20261003.py --max-calls 1000 --stale-days 30   # 스윕(03:15) 450건과 합쳐 일 1,500건 이내(한도 카운터가 프로세스별이라 명시적으로 나눔)
    step $PY $R/compare_db_vs_fnguide_raw_20261003.py                        # 원문 ↔ DB 대조(읽기 전용) → fnguide_raw_compare_*.json/csv
    ;;
  *) echo "usage: $0 dart|fnguide"; exit 2 ;;
esac
