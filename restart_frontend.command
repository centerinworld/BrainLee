#!/bin/zsh
source ~/.zshrc
echo "▶ 프론트엔드 preview 재시작 중..."
kill $(cat /Volumes/Realtek_NVME/stock_dashboard/runtime/logs/frontend.pid) 2>/dev/null || true
sleep 1
cd /Volumes/Realtek_NVME/stock_dashboard/runtime/frontend
nohup npm run preview > /Volumes/Realtek_NVME/stock_dashboard/runtime/logs/frontend.log 2>&1 &
echo $! > /Volumes/Realtek_NVME/stock_dashboard/runtime/logs/frontend.pid
echo "✅ 완료! PID: $(cat /Volumes/Realtek_NVME/stock_dashboard/runtime/logs/frontend.pid)"
echo "브라우저에서 http://localhost:5173 를 강력 새로고침(Cmd+Shift+R) 해주세요."
sleep 3
