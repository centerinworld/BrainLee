#!/bin/bash
# ============================================================
# run_full_backfill.sh — 전체 누락 데이터 수집 마스터 스크립트
# ============================================================
# 실행: bash /Volumes/Realtek_NVME/stock_dashboard/runtime/run_full_backfill.sh
#
# 수집 대상 (누락 현황 기준 2026-05-06):
#   [1] Naver 수급 5년치   — 2021~2023 약 507일 누락 (inst/frn net_buy)
#   [2] DART 수주공시       — 2024-07~2025-12 완전 누락 (0건)
#   [3] 대차잔고 5년치      — short_rank_daily 16일치만 존재
#   [4] DART 5년치          — 2021~2024-06 보완
#   [5] BigQuery 전체 동기화 — 수집 완료 후 실행
#
# 예상 소요: 4~8시간 (단계적 순차 실행, DB 충돌 방지)
# ============================================================

# set -e 제거 — 개별 수집 실패가 전체를 중단하지 않도록

cd /Volumes/Realtek_NVME/stock_dashboard/runtime || { echo "디렉토리 없음"; exit 1; }
source venv/bin/activate || { echo "venv 활성화 실패"; exit 1; }

LOG_DIR="/Volumes/Realtek_NVME/stock_dashboard/runtime/logs"
mkdir -p "$LOG_DIR"
TS=$(date +%Y%m%d_%H%M%S)

echo "========================================================"
echo "  전체 데이터 백필 시작: $(date)"
echo "========================================================"

# ── [1] Naver 수급 5년치 (백그라운드) ────────────────────────
# ⚠️ 서버 스케줄러와 DB 충돌 없음 (Naver 수급은 별도 UPDATE 경로)
echo ""
echo "[1/5] Naver 기관/외국인 수급 5년치 수집..."
echo "  대상: 전종목 ~2,500개 / 예상: 3~5시간"
python3 collect_naver_investor.py --years 5 \
  >> "$LOG_DIR/naver_5y_${TS}.log" 2>&1 &
NAVER_PID=$!
echo "  PID: $NAVER_PID"
echo "  로그: tail -f $LOG_DIR/naver_5y_${TS}.log"

# ── [2] DART 수주공시 2024-07~2025-12 백필 ────────────────
# ⚠️ 서버와 DB 공유 — busy_timeout=60초 설정됨
echo ""
echo "[2/5] DART 수주공시 누락구간 백필 (2024-07~2025-12)..."
python3 /Volumes/Realtek_NVME/stock_dashboard/runtime/dart_backfill_2024_2025.py >> "$LOG_DIR/dart_2024_2025_${TS}.log" 2>&1 &
DART_PID=$!
echo "  PID: $DART_PID"
echo "  로그: tail -f $LOG_DIR/dart_2024_2025_${TS}.log"

# ── [3] 대차잔고 5년치 ────────────────────────────────────
# ⚠️ apis.data.go.kr — 서버에서 DNS 접근 필요
echo ""
echo "[3/5] 대차잔고 5년치 백필 (rank + svc + sector)..."
echo "  ※ apis.data.go.kr API 사용"
echo "  예상: 약 1~2시간"
python3 /Volumes/Realtek_NVME/stock_dashboard/runtime/collect_short_5years.py \
  --mode all \
  >> "$LOG_DIR/short_5y_${TS}.log" 2>&1 &
SHORT_PID=$!
echo "  PID: $SHORT_PID"
echo "  로그: tail -f $LOG_DIR/short_5y_${TS}.log"

echo ""
echo "========================================================"
echo "  백그라운드 수집 시작:"
echo "    Naver 수급:    PID $NAVER_PID"
echo "    DART 2024-25:  PID $DART_PID"
echo "    대차잔고:      PID $SHORT_PID"
echo ""
echo "  로그 확인:"
echo "    tail -f $LOG_DIR/naver_5y_${TS}.log"
echo "    tail -f $LOG_DIR/dart_2024_2025_${TS}.log"
echo "    tail -f $LOG_DIR/short_5y_${TS}.log"
echo ""
echo "  DART 2024-25 완료 대기 중 (~30분)..."
echo "========================================================"

# DART 2024-25 완료 대기
wait $DART_PID 2>/dev/null
DART_EXIT=$?
if [ $DART_EXIT -eq 0 ]; then
    echo "✅ DART 2024-07~2025-12 백필 완료 ($(date))"
    DART_CNT=$(python3 -c "
import sqlite3
conn = sqlite3.connect('/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db', timeout=30)
try:
    r = conn.execute(\"SELECT COUNT(*) FROM dart_contracts WHERE disclosed_at >= '20240701' AND disclosed_at <= '20251231'\").fetchone()
    print(r[0])
except: print('?')
conn.close()
" 2>/dev/null)
    echo "   수집 건수: ${DART_CNT}건"
else
    echo "⚠️  DART 백필 오류 (exit $DART_EXIT) — 로그 확인"
fi

# [4] DART 5년치 추가 (순차 실행 — DB 부하 분산)
echo ""
echo "[4/5] DART 수주공시 2021~2024-06 보완..."
if ps aux | grep -v grep | grep "dart_backfill\|collect_dart_contracts" | grep -q python 2>/dev/null; then
    echo "  → 이미 실행 중. 스킵."
else
    python3 /Volumes/Realtek_NVME/stock_dashboard/runtime/dart_backfill_5years.py \
      >> "$LOG_DIR/dart_5y_${TS}.log" 2>&1 &
    DART5_PID=$!
    echo "  PID: $DART5_PID"
    echo "  로그: tail -f $LOG_DIR/dart_5y_${TS}.log"
fi

# 대차잔고 완료 대기
wait $SHORT_PID 2>/dev/null
SHORT_EXIT=$?
if [ $SHORT_EXIT -eq 0 ]; then
    echo "✅ 대차잔고 5년치 완료 ($(date))"
    python3 - 2>/dev/null <<'PYEOF'
import sqlite3
conn = sqlite3.connect('/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db', timeout=30)
for t in ['short_rank_daily','short_sell_daily','short_sector_daily']:
    try:
        r = conn.execute(f'SELECT COUNT(DISTINCT bas_dt), MIN(bas_dt), MAX(bas_dt) FROM {t}').fetchone()
        print(f'  {t}: {r[0]}일, {r[1]}~{r[2]}')
    except Exception as e:
        print(f'  {t}: {e}')
conn.close()
PYEOF
else
    echo "⚠️  대차잔고 백필 오류 (exit $SHORT_EXIT)"
fi

echo ""
echo "========================================================"
echo "  ⏳ Naver 수급 수집 계속 진행 중 (PID: $NAVER_PID)"
echo ""
echo "  Naver 완료 후 실행할 것:"
echo "  [5] BigQuery 전체 동기화:"
echo "      cd /Volumes/Realtek_NVME/stock_dashboard/runtime && source venv/bin/activate"
echo "      python3 bigquery_sync.py --mode full"
echo ""
echo "  [6] US 주식 5년치 (별도 서비스):"
echo "      cd /Volumes/Realtek_NVME/us_market_dashboard && source venv/bin/activate"
echo "      python3 backfill.py --mode all"
echo "========================================================"
