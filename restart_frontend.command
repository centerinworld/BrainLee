#!/bin/zsh
source ~/.zshrc
echo "▶ 프론트엔드 preview 재시작 중..."
kill $(cat /Applications/stock_dashboard/logs/frontend.pid) 2>/dev/null || true
sleep 1
cd /Applications/stock_dashboard/frontend
nohup npm run preview > /Applications/stock_dashboard/logs/frontend.log 2>&1 &
echo $! > /Applications/stock_dashboard/logs/frontend.pid
echo "✅ 완료! PID: $(cat /Applications/stock_dashboard/logs/frontend.pid)"
echo "브라우저에서 http://localhost:5173 를 강력 새로고침(Cmd+Shift+R) 해주세요."
sleep 3
