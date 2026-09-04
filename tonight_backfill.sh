#!/bin/bash
# ============================================================
# tonight_backfill.sh — 새벽 자동 백필 (1회 실행용)
# ============================================================
# 수집 순서:
#   [1] Naver 수급 5년치   (3~5시간)
#   [2] DART 2024-07~2025-12 백필 (~30분)
#   [3] DART 5년치 보완    (~2시간)
#   [4] 대차잔고 5년치     (~1~2시간)
#   [5] KIS OHLCV 최근 7일 갱신 (누락 보완)
#   [6] NPS V2 고용 가용 과거월 백필 (API 제공 범위만)
# SQLite 안정성을 위해 stock.db writer 작업은 순차 실행한다.
# ============================================================

source ~/.zshrc && cd /Volumes/Realtek_NVME/stock_dashboard/runtime && source venv/bin/activate

# cron은 launchd와 별도 환경이라 PYTHONPATH가 비어 있음 — 아래 python3 호출들이
# stock.db를 열 때 PostgreSQL로 라우팅되도록 명시적으로 export한다.
export PYTHONPATH="/Volumes/Realtek_NVME/stock_dashboard/runtime/runtime_pg_bootstrap${PYTHONPATH:+:$PYTHONPATH}"

LOG_DIR="/Volumes/Realtek_NVME/stock_dashboard/runtime/logs"
mkdir -p "$LOG_DIR"
TS=$(date +%Y%m%d_%H%M%S)

echo "========================================================"
echo "  새벽 자동 백필 시작: $(date)"
echo "========================================================"

# [1] Naver 수급 5년치 (순차 — 3~5시간)
echo "[1/6] Naver 기관/외국인 수급 5년치..."
python3 /Volumes/Realtek_NVME/stock_dashboard/runtime/collect_naver_investor.py --years 5 \
  >> "$LOG_DIR/naver_5y_${TS}.log" 2>&1
echo "  완료 (종료코드: $?) | log: $LOG_DIR/naver_5y_${TS}.log"

# [2] DART 2024-07~2025-12 백필 (순차 — ~30분)
echo "[2/6] DART 수주공시 2024-07~2025-12..."
python3 /Volumes/Realtek_NVME/stock_dashboard/runtime/dart_backfill_2024_2025.py \
  >> "$LOG_DIR/dart_2024_2025_${TS}.log" 2>&1
echo "  완료 (종료코드: $?)"

# [3] DART 5년치 보완 (순차)
echo "[3/6] DART 수주공시 5년치 보완..."
python3 /Volumes/Realtek_NVME/stock_dashboard/runtime/dart_backfill_5years.py \
  >> "$LOG_DIR/dart_5y_${TS}.log" 2>&1
echo "  완료 (종료코드: $?) | log: $LOG_DIR/dart_5y_${TS}.log"

# [4] 대차잔고 5년치 (순차)
echo "[4/6] 대차잔고 5년치..."
python3 /Volumes/Realtek_NVME/stock_dashboard/runtime/collect_short_5years.py --mode all \
  >> "$LOG_DIR/short_5y_${TS}.log" 2>&1
echo "  완료 (종료코드: $?) | log: $LOG_DIR/short_5y_${TS}.log"

# [5] KIS OHLCV 최근 7일 갱신 (누락 보완)
echo "[5/6] KIS OHLCV 최근 7일 갱신..."
python3 /Volumes/Realtek_NVME/stock_dashboard/runtime/collect_kis_ohlcv.py --days 7 \
  >> "$LOG_DIR/kis_ohlcv_7d_${TS}.log" 2>&1
echo "  완료 (종료코드: $?)"

# [6] NPS V2 고용 최근 3개월 보완
# NPS는 월별 seq가 달라 최신 seq 단순 조회만으로는 전월 데이터가 비는 경우가 있다.
echo "[6/6] NPS V2 고용 최근 3개월 보완..."
python3 /Volumes/Realtek_NVME/stock_dashboard/runtime/employment_monitor/collect_nps_monthly.py \
  --historical --months-back 3 \
  >> "$LOG_DIR/nps_v2_historical_${TS}.log" 2>&1
echo "  완료 (종료코드: $?) | log: $LOG_DIR/nps_v2_historical_${TS}.log"

echo ""
echo "  모든 수집이 순차 완료되었습니다. 브라우저를 새로고침하세요."
echo "========================================================"
echo "  새벽 백필 시작 완료: $(date)"
echo "========================================================"

# crontab에서 자기 자신을 제거 (1회 실행 후 자동 삭제)
crontab -l 2>/dev/null | grep -v "tonight_backfill.sh" | crontab -
echo "  crontab 항목 자동 삭제 완료"
