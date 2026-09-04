#!/bin/bash
# FnGuide 재무 수집 재개 스크립트
# - check_financial_integrity.py 완료 후 실행
# - migrate_datasource.py로 data_source 마킹 후 실행
# - 서버 재시작 후 실행 (crud.py 보호 로직 적용)
#
# 사용: bash run_fnguide_resume.sh

set -e
cd /Volumes/Realtek_NVME/stock_dashboard/runtime

LOGDIR="logs"
VERIFY_DIR="verification"
mkdir -p "$LOGDIR" "$VERIFY_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "========================================"
echo " FnGuide 재무 수집 재개"
echo " 시작: $(date)"
echo "========================================"

# ── Step 1: check_financial_integrity 실행 중이면 대기 ──────────────────────
while pgrep -f "check_financial_integrity" > /dev/null; do
    echo "[대기] check_financial_integrity.py 실행 중 — 60초 후 재확인..."
    sleep 60
done
echo "[OK] check_financial_integrity 완료 확인"

# ── Step 2: DB 마이그레이션 (data_source 컬럼 + fnguide 마킹) ──────────────
echo ""
echo "--- Step 2: DB 마이그레이션 ---"
python3 migrate_datasource.py 2>&1 | tee -a "$LOGDIR/migrate_datasource_${TIMESTAMP}.log"

# ── Step 3: 서버 재시작 (crud.py 보호 로직 적용) ───────────────────────────
echo ""
echo "--- Step 3: 서버 재시작 ---"
launchctl stop com.stock-dashboard.local 2>/dev/null || true
sleep 5
launchctl start com.stock-dashboard.local 2>/dev/null || true
sleep 8

# 서버 응답 확인
if curl -sf http://localhost:8000/api/dashboard/stats > /dev/null; then
    echo "[OK] 서버 재시작 완료"
else
    echo "[경고] 서버 응답 없음 — 계속 진행"
fi

# ── Step 4: FnGuide 배치 수집 (미수집 2,008종목) ───────────────────────────
echo ""
echo "--- Step 4: FnGuide 배치 수집 시작 ---"
echo "  대상: financial_source_snapshot 없는 종목 우선"
echo "  예상 소요: 약 3~4시간 (2,000종목 × ~6초)"
echo ""

# stale-days=365: 1년 이내 수집된 종목은 스킵 → 미수집 종목(has_snapshot=0) 우선 처리
python3 -m collectors.fnguide_financial_collector \
    --limit 9999 \
    --stale-days 365 \
    2>&1 | tee -a "$LOGDIR/fnguide_resume_${TIMESTAMP}.log"

# ── Step 5: After-snapshot 저장 ─────────────────────────────────────────────
echo ""
echo "--- Step 5: After-snapshot 저장 ---"
python3 - << 'PYEOF'
import sqlite3, json, datetime, os

DB_PATH = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"
VERIFY_DIR = "/Volumes/Realtek_NVME/stock_dashboard/runtime/verification"
TIMESTAMP = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

conn = sqlite3.connect(DB_PATH)
snap = {}

# financial_data by source
rows = conn.execute("""
    SELECT COALESCE(data_source,'unknown'), COUNT(*), COUNT(DISTINCT stock_code)
    FROM financial_data GROUP BY 1
""").fetchall()
snap['financial_data_by_source'] = {r[0]: {'records': r[1], 'stocks': r[2]} for r in rows}

# cash_flow_data by source
rows2 = conn.execute("""
    SELECT COALESCE(data_source,'unknown'), COUNT(*), COUNT(DISTINCT stock_code)
    FROM cash_flow_data GROUP BY 1
""").fetchall()
snap['cash_flow_data_by_source'] = {r[0]: {'records': r[1], 'stocks': r[2]} for r in rows2}

# financial_source_snapshot
r3 = conn.execute("SELECT COUNT(*), COUNT(DISTINCT stock_code) FROM financial_source_snapshot WHERE data_source='fnguide'").fetchone()
snap['fnguide_snapshot'] = {'total': r3[0], 'stocks': r3[1]}

# NULL 현황
fn_null = conn.execute("""
    SELECT
        SUM(CASE WHEN revenue IS NULL THEN 1 ELSE 0 END)          rev_null,
        SUM(CASE WHEN operating_profit IS NULL THEN 1 ELSE 0 END) op_null,
        SUM(CASE WHEN net_income IS NULL THEN 1 ELSE 0 END)       ni_null,
        SUM(CASE WHEN total_assets IS NULL THEN 1 ELSE 0 END)     asset_null,
        COUNT(*) total
    FROM financial_data WHERE data_source = 'fnguide'
""").fetchone()
snap['fnguide_null_check'] = {
    'total': fn_null[4], 'rev_null': fn_null[0], 'op_null': fn_null[1],
    'ni_null': fn_null[2], 'asset_null': fn_null[3],
}

# profiles
import json as _json
import os
profiles_path = "/Volumes/Realtek_NVME/stock_dashboard/runtime/config/financial_profiles.json"
if os.path.exists(profiles_path):
    with open(profiles_path) as f:
        profiles = _json.load(f)
    snap['profiles_count'] = len([k for k in profiles if k != '_meta'])

snap['snapshot_at'] = datetime.datetime.now().isoformat()
snap['label'] = 'AFTER_FNGUIDE_RESUME'

conn.close()

out_path = f"{VERIFY_DIR}/after_fnguide_resume_{TIMESTAMP}.json"
with open(out_path, 'w') as f:
    _json.dump(snap, f, ensure_ascii=False, indent=2)

print(f"After-snapshot 저장: {out_path}")
print(json.dumps(snap, ensure_ascii=False, indent=2))
PYEOF

echo ""
echo "========================================"
echo " 완료: $(date)"
echo " 검증 파일: verification/"
echo "========================================"
