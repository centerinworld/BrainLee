#!/bin/bash
# FnGuide 프로세스 종료 대기 후 현금흐름 누적→증분 변환 실행
LOG=/Applications/stock_dashboard/logs/cf_conversion_$(date +%Y%m%d_%H%M%S).log
cd /Applications/stock_dashboard

echo "$(date): FnGuide 프로세스 종료 대기 중..." | tee $LOG

# fnguide_financial_collector.py 프로세스가 없어질 때까지 대기
until ! pgrep -f "fnguide_financial_collector" > /dev/null 2>&1; do
  sleep 30
done

echo "$(date): FnGuide 종료 확인. 현금흐름 변환 시작..." | tee -a $LOG
python3 scripts/convert_cf_cumulative_to_quarterly.py --years 2015-2025 2>&1 | tee -a $LOG
echo "$(date): 변환 완료." | tee -a $LOG
