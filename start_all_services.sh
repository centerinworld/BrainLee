
# 5. 파일 동기화 (AI System → 로컬 codex)
echo "🔹 [4/4] 통합 파일 동기화 실행..."
bash "/Volumes/Realtek_NVME/AI System/sync_unified.sh"

# 6. Gems 일일 분석 크론 등록 (매일 오전 6시 - 한도 초기화 이후)
CRON_CMD="0 6 * * * cd /Volumes/Realtek_NVME/stock_dashboard && python3 gemini_gems_worker.py >> /Volumes/Realtek_NVME/stock_dashboard/logs/gems_daily.log 2>&1"
( crontab -l 2>/dev/null | grep -v "gemini_gems_worker"; echo "$CRON_CMD" ) | crontab - 2>/dev/null
echo "   ✅ Gems 일일 분석 크론 등록 완료 (매일 06:00)"

echo "=========================================================="
echo "✅ 모든 AI 시스템 서비스가 정상 가동 중입니다."
echo "   - AI 관제 센터: https://newsinfo.cloud/kai/ (또는 http://localhost:5500/kai/)"
echo "   - 관리자 콘솔:   https://newsinfo.cloud/ (또는 http://localhost:5500/)"
echo "   - API 서버:     https://api.newsinfo.cloud/ (또는 http://localhost:8011/)"
echo "   ※ localhost와 외부 도메인은 완전 동일한 파일을 서빙합니다 (AI System이 Master)"
echo "=========================================================="

# === 추가: 파일 동기화 (AI System → 로컬 codex) ===
bash "/Volumes/Realtek_NVME/AI System/sync_unified.sh"
