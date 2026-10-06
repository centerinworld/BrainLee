#!/bin/zsh
# 숫자 데이터 원문 재수집 — 매일 한도 안에서 이어 받기(2026-10-03, docs/FINANCIAL_STATEMENTS.md §9)
# launchd com.stock-dashboard.numeric-recollect 가 호출한다(재부팅에도 유지).
#   dart    (00:20) : DART 키1→키3→키4→키2 순차 사용(KEY2는 일괄 상한으로 공시 몫 보호). 각 단계는 한도 소진 시 스스로 종료 → 다음 날 이어서.
#   fnguide (04:00) : FnGuide wcomp 원문 저장(연결·별도·연간·분기). 일 1,500 공유 한도 중 기존 스윕(03:15) 이후 잔여 사용.
# 수집·대조가 기본. 예외(사용자 승인 2026-10-05): ETF 원주가 반영·신규 상장 편입은 안전장치 포함 자동 적용.
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
    step $PY $R/fetch_dart_business_docs_20261005.py --years 2021,2022 --exit-on-quota   # 사업보고서 본문 원문 — XBRL 주석 없는 2021~22 우선(사용자 지시 2026-10-05)
    step $PY $R/fetch_dart_cashflow_20261002.py --years 2021-2022 --prev-only   # 2021~22 연간 전체 계정 원문(매출채권·차입금·이자·매출원가 대조용)
    step $PY scripts/collect_dart_dividends.py --year 2021 --year 2022 --year 2023 --year 2024 --year 2025   # 배당 2021~(이미 받은 종목·연도 건너뜀)
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
    step $PY $R/build_geo_revenue_xbrl_20261005.py                          # 국내/해외 매출(XBRL 지역 주석, 합계 항등식 확인) → revenue_geography
    step $PY $R/fetch_dart_business_docs_20261005.py --exit-on-quota         # 사업보고서 본문 원문 2023~(남은 한도)
    step $PY $R/parse_business_docs_20261005.py                               # 연구개발비·가동률·원재료 가격·내수/수출·원가 성격·제품별 매출(2021~22)
    step $PY $R/build_extra_accounts_20261005.py                              # 매출채권·차입금·사채·금융원가·이자(FnGuide 대조)
    step $PY $R/build_dep_capex_components_20261003.py                       # 감가상각·CapEx 구성요소 테이블
    step $PY $R/build_financial_pit_20261003.py                              # 시점(PIT) 사실 테이블: 최초 공시값·재작성값 이력
    ;;
  fnguide)
    step $PY $R/fetch_fnguide_raw_20261003.py --max-calls 1000 --stale-days 30   # 스윕(03:15) 450건과 합쳐 일 1,500건 이내(한도 카운터가 프로세스별이라 명시적으로 나눔)
    step $PY $R/compare_db_vs_fnguide_raw_20261003.py                        # 원문 ↔ DB 대조(읽기 전용) → fnguide_raw_compare_*.json/csv
    step $PY $R/fetch_krx_openapi_20261005.py --kind all --max-calls 8000      # KRX Open API: 공식 주가 빈 날짜(2010~) + 파생지수·선물 이어 받기(2026-10-05)
    step $PY scripts/ops/sync_new_listings.py --apply                         # 신규 상장 매일 편입(마스터) + 상장일~가격 첫 날 KIS 원주가 채움(2026-10-05, 예전엔 월 1회 편입)
    step $PY $R/fetch_kis_raw_daily_20261004.py --etf-all --max-codes 60       # ETF·ETN 원주가(KIS) 원문 — 하루 60종목(~1시간), 가격 대조 근거
    step $PY $R/price_raw_basis_audit_20261004.py                            # 가격 원주가 3소스(공식·marcap·KIS) 전수 감사(읽기 전용) → price_raw_basis_audit_20261004/summary.json
    step $PY $R/apply_price_kis_tiebreak_20261004.py --etf-adjusted --daily --apply   # ETF 배당 조정값 → KIS 원주가(조정 비율 0.75~1.0·OHLC 정합·감사 6시간 이내만, 2026-10-05 매일 자동 승인)
    step $PY $R/verify_backlog_identity_20261004.py                          # 수주잔고 원문 항등식 측정(읽기 전용)
    step $PY $R/apply_restated_annual_20261004.py --apply                     # 재작성값 중 외부(FnGuide·네이버)가 확인한 칸만 반영(2026-10-06 — FnGuide 원문이 매일 늘어남)
    step $PY scripts/ops/check_financial_anomalies_daily.py                   # 재무 이상값 감시(보고만): 단위 300배·미래 기간·연결/별도 1,000배 → data_anomaly_daily
    step $PY $R/build_field_verification_20261003.py                         # 현행 기준 필드 확정 상태(financial_field_verification) — 화면 품질 등급 근거
    ;;
  *) echo "usage: $0 dart|fnguide"; exit 2 ;;
esac
