#!/bin/bash
# ============================================================
# 5년치 데이터 전체 백필 스크립트
# 실행: bash /Volumes/Realtek_NVME/stock_dashboard/runtime/backfill_5years.sh
# 예상 소요: 3~6시간 (네트워크 속도 따라)
# ============================================================

set -e
source ~/.zshrc 2>/dev/null || true
cd /Volumes/Realtek_NVME/stock_dashboard/runtime
source venv/bin/activate

LOG_DIR="/Volumes/Realtek_NVME/stock_dashboard/runtime/logs"
mkdir -p "$LOG_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "========================================================"
echo "  5년치 데이터 백필 시작: $(date)"
echo "========================================================"

# ── STEP 1: 기관/외국인 수급 5년치 (Naver Finance) ────────────
echo ""
echo "[STEP 1/4] 기관/외국인 수급 5년치 수집 시작 (Naver Finance)..."
echo "  예상 시간: 2~4시간 (전종목 ~2,500개)"
python3 collect_naver_investor.py --years 5 \
  > "$LOG_DIR/naver_investor_5y_${TIMESTAMP}.log" 2>&1 &
NAVER_PID=$!
echo "  PID: $NAVER_PID → 로그: $LOG_DIR/naver_investor_5y_${TIMESTAMP}.log"

# ── STEP 2: DART 수주공시 누락구간 백필 (2024-07~2025-12) ─────
echo ""
echo "[STEP 2/4] DART 수주공시 2024-07~2025-12 백필..."
python3 -c "
import sys
sys.path.insert(0, '/Volumes/Realtek_NVME/stock_dashboard/runtime')
import asyncio, logging, os
os.chdir('/Volumes/Realtek_NVME/stock_dashboard/runtime')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
from collectors.dart_contract_collector import collect_dart_contracts

# 2024-07-01 ~ 2025-12-31 (누락 구간)
periods = [
    ('20240701', '20241231'),
    ('20250101', '20250630'),
    ('20250701', '20251231'),
]
for bgn, end in periods:
    print(f'  수집: {bgn} ~ {end}')
    asyncio.run(collect_dart_contracts(
        days=0, min_signal=1, skip_ai=True,
        bgn_str=bgn, end_str=end
    ))
    print(f'  완료: {bgn} ~ {end}')
print('DART 백필 완료')
" > "$LOG_DIR/dart_backfill_2024_2025_${TIMESTAMP}.log" 2>&1 &
DART_PID=$!
echo "  PID: $DART_PID → 로그: $LOG_DIR/dart_backfill_2024_2025_${TIMESTAMP}.log"

# ── STEP 3: AI 점수 보완 (규칙기반 → AI 분석) ─────────────────
echo ""
echo "[STEP 3/4] DART 기수집 건 AI 분석 보완 (ai_score=0 건)..."
python3 -c "
import sqlite3, sys, os, logging
os.chdir('/Volumes/Realtek_NVME/stock_dashboard/runtime')
sys.path.insert(0, '.')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')

conn = sqlite3.connect('stock.db')
missing = conn.execute('''
    SELECT COUNT(*) FROM dart_contracts
    WHERE (ai_score IS NULL OR ai_score = 0)
    AND disclosed_at >= '20210101'
''').fetchone()[0]
conn.close()
print(f'AI 점수 미처리 건수: {missing}')
if missing > 0:
    print('  → skip_ai=True로 수집된 건들, 별도 AI 분석 필요')
    print('  → 현재 규칙기반 점수로 BigQuery 업로드 진행')
" 2>&1

# ── STEP 4: 재무제표 누락분 보완 (최근 2분기) ─────────────────
echo ""
echo "[STEP 4/4] 재무제표 최신 데이터 보완..."
python3 -c "
import subprocess, sys
result = subprocess.run(
    [sys.executable, 'collect_dart_cashflow_batch.py',
     '--fill-missing', '--years', '1'],
    capture_output=True, text=True, timeout=3600
)
print(result.stdout[-3000:] if result.stdout else '출력 없음')
if result.returncode != 0:
    print('오류:', result.stderr[-1000:])
" > "$LOG_DIR/cashflow_fill_${TIMESTAMP}.log" 2>&1 &
CF_PID=$!
echo "  PID: $CF_PID → 로그: $LOG_DIR/cashflow_fill_${TIMESTAMP}.log"

echo ""
echo "========================================================"
echo "  백그라운드 수집 중..."
echo "  진행상황 확인:"
echo "    tail -f $LOG_DIR/naver_investor_5y_${TIMESTAMP}.log"
echo "    tail -f $LOG_DIR/dart_backfill_2024_2025_${TIMESTAMP}.log"
echo ""
echo "  완료 후 BigQuery 전체 업로드:"
echo "    python3 bigquery_sync.py --mode full"
echo "========================================================"

# DART 백필 완료 대기 (빠름 ~30분)
echo ""
echo "DART 백필 완료 대기 중..."
wait $DART_PID
echo "✅ DART 백필 완료 ($(date))"

# 수급 수집은 백그라운드 유지 (오래 걸림)
echo "⏳ 수급 수집 계속 진행 중 (PID: $NAVER_PID)"
echo "   완료 후 python3 bigquery_sync.py --mode full 실행하세요"
