#!/bin/zsh
# 숫자 데이터 원문 재수집 — 매일 한도 안에서 이어 받기(2026-10-03, docs/FINANCIAL_STATEMENTS.md §9)
# launchd com.stock-dashboard.numeric-recollect 가 호출한다(재부팅에도 유지).
#   dart    (00:20) : DART 키1→키3→키4→키2 순차 사용(KEY2는 일괄 상한으로 공시 몫 보호). 각 단계는 한도 소진 시 스스로 종료 → 다음 날 이어서.
#   fnguide (04:00) : FnGuide wcomp 원문 저장(연결·별도·연간·분기). 일 1,500 공유 한도 중 기존 스윕(03:15) 이후 잔여 사용.
# 수집·대조만 한다(재무 운영 테이블 미변경, 검증 상태 테이블만 갱신). 값 적용은 사람이 dry-run 확인 후 별도 실행.
cd /Volumes/Realtek_NVME/stock_dashboard/runtime || exit 1
export PYTHONPATH=runtime_pg_bootstrap:.
LOG=research_outputs/financial_rereview_20261002/daily_numeric_recollect.log
PY=venv/bin/python
R=scripts/review
step() {
  echo "$(date '+%F %T') ▶ $*" >> $LOG
  "$@" >> $LOG 2>&1
  local rc=$?
  echo "$(date '+%F %T') ◀ rc=$rc" >> $LOG
  return $rc
}

case "$1" in
  dart)
    step $PY $R/test_quarterly_xbrl_20261003.py                              # 1회: 대형사 분기 주석 XBRL 시험(결과 있으면 건너뜀)
    step $PY $R/fetch_dart_cashflow_20261002.py --repair-parent              # 2023+ 지배주주 보완
    step $PY $R/fetch_dart_cashflow_20261002.py --prev-only                  # 2023+ 재작성값(전기 칸)
    step $PY $R/fetch_xbrl_depreciation_20261003.py --exit-on-quota          # 감가상각 XBRL
    step $PY $R/fetch_xbrl_depreciation_20261003.py --exit-on-quota --redo-missing-adj   # 조정 감가상각 누락분 재수집(원문 zip 저장)
    step $PY $R/fetch_quarterly_xbrl_depreciation_20261003.py --exit-on-quota # 자산 2조↑ 분기·반기 XBRL 감가상각 구성요소(YTD, 테이블에서 3개월 차분)
    step $PY $R/fetch_dart_cashflow_20261002.py --years 2016-2022            # 2016~2022 본 수집
    step $PY $R/fetch_dart_cashflow_20261002.py --years 2016-2022 --repair-parent
    step $PY $R/fetch_dart_cashflow_20261002.py --years 2016-2022 --prev-only
    step $PY $R/fetch_dart_cashflow_20261002.py --refetch-raw                # 2023+ 원문 계정 행 보충(재고자산·CapEx 세부 재파싱용, 낮은 우선순위)
    step $PY $R/build_dep_capex_components_20261003.py                       # 감가상각·CapEx 구성요소 테이블
    step $PY $R/build_financial_pit_20261003.py                              # 시점(PIT) 사실 테이블: 최초 공시값·재작성값 이력
    ;;
  fnguide)
    step $PY $R/fetch_fnguide_raw_20261003.py --max-calls 1000 --stale-days 30   # 스윕(03:15) 450건과 합쳐 일 1,500건 이내(한도 카운터가 프로세스별이라 명시적으로 나눔)
    step $PY $R/compare_db_vs_fnguide_raw_20261003.py                        # 원문 ↔ DB 대조(읽기 전용) → fnguide_raw_compare_*.json/csv
    step $PY $R/fetch_kis_raw_daily_20261004.py --etf-all --max-codes 60       # ETF·ETN 원주가(KIS) 원문 — 하루 60종목(~1시간), 가격 대조 근거
    step $PY $R/build_field_verification_20261003.py                         # 현행 기준 필드 확정 상태(financial_field_verification) — 화면 품질 등급 근거
    ;;
  *) echo "usage: $0 dart|fnguide"; exit 2 ;;
esac
