#!/bin/bash
# insider(41490 -> 42397) 및 credit(45190) 완료 대기 후 BQ 동기화
set -e
cd /Volumes/Realtek_NVME/stock_dashboard/runtime

INSIDER_PID=42397
CREDIT_PID=45190

echo "$(date): 백그라운드 수집 대기 중 (insider PID=$INSIDER_PID, credit PID=$CREDIT_PID)..."

wait_for_pid() {
    local pid=$1
    local name=$2
    while kill -0 "$pid" 2>/dev/null; do
        echo "$(date): $name (PID=$pid) 진행 중..."
        sleep 30
    done
    echo "$(date): $name 완료"
}

wait_for_pid $INSIDER_PID "dart_insider_holdings 수집"
wait_for_pid $CREDIT_PID "kiwoom_credit_balance 수집"

echo "$(date): 두 수집 완료. BQ 동기화 시작..."

echo "$(date): [1/3] dart_insider_holdings → BQ 동기화"
python3 bigquery_sync.py --mode table --table dart_insider_holdings
echo "$(date): [2/3] kiwoom_credit_balance → BQ 동기화"
python3 bigquery_sync.py --mode table --table kiwoom_credit_balance
echo "$(date): [3/3] BQ views 재생성"
python3 bigquery_sync.py --mode views
echo "$(date): 모든 작업 완료!"
