#!/bin/zsh
# 새벽 3시 자동 데이터 수집 스크립트
# 실행 대상: DART 재무, 공시, 공공데이터, 주가 전체
# crontab 등록: 0 3 * * 1-5 /Volumes/Realtek_NVME/stock_dashboard/runtime/cron_3am.sh >> /Volumes/Realtek_NVME/stock_dashboard/runtime/cron_3am.log 2>&1

PROJECT_ROOT="/Volumes/Realtek_NVME/stock_dashboard/runtime"
PYTHON="$PROJECT_ROOT/venv/bin/python3"
LOG_DATE=$(date '+%Y-%m-%d %H:%M:%S')

# cron은 launchd와 별도 환경이라 PYTHONPATH가 비어 있음 — stock.db를 여는
# 하위 파이썬 프로세스가 PostgreSQL로 라우팅되도록 명시적으로 export한다.
export PYTHONPATH="$PROJECT_ROOT/runtime_pg_bootstrap${PYTHONPATH:+:$PYTHONPATH}"

echo "===== [$LOG_DATE] 새벽 3시 배치 시작 ====="

# 백엔드가 실행 중인지 확인 (포트 8000)
if ! curl -s http://127.0.0.1:8000/ > /dev/null 2>&1; then
  echo "[경고] 백엔드 서버가 실행 중이지 않습니다. 스크립트를 종료합니다."
  exit 1
fi

# 공공데이터 수집 (KRX, 공시 등)
# 새벽 3시에는 전일(영업일) 데이터를 수집 (장 개장 전이므로 오늘 데이터 없음)
PREV_DATE=$(date -v-1d '+%Y%m%d' 2>/dev/null || date -d "yesterday" '+%Y%m%d' 2>/dev/null || date '+%Y%m%d')
echo "[$LOG_DATE] 공공데이터 수집 시작 (날짜: $PREV_DATE)..."
cd "$PROJECT_ROOT" && $PYTHON public_data_collector.py --date "$PREV_DATE" >> "$PROJECT_ROOT/public_data.log" 2>&1
echo "[$LOG_DATE] 공공데이터 수집 완료"

# 주가 데이터 전체 수집 (watchlist + universe)
echo "[$LOG_DATE] 주가 데이터 수집 시작..."
curl -s -X POST http://127.0.0.1:8000/api/commands/collect-all > /dev/null 2>&1 || true
echo "[$LOG_DATE] 주가 데이터 수집 요청 완료"

echo "===== 배치 완료 ====="
