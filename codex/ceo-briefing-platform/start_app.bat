@echo off
chcp 65001 >nul
echo ==============================================
echo   CEO Briefing Platform - 시작 스크립트
echo ==============================================
echo.

echo [1/2] 백엔드 서버(FastAPI)를 실행합니다...
:: 새 창(cmd)을 띄워서 백엔드 서버를 백그라운드처럼 실행합니다.
start "Backend API Server" cmd /k "cd backend && .venv\Scripts\activate && uvicorn main:app --port 8011 --reload"

:: 서버가 켜질 때까지 잠시(3초) 대기
timeout /t 3 /nobreak >nul

echo [2/2] 프론트엔드 화면을 브라우저에서 엽니다...
start frontend\index.html

echo.
echo 실행 완료! 이 창은 곧 닫힙니다.
echo (참고: 같이 켜진 'Backend API Server' 검은색 창을 닫으면 서버가 꺼집니다.)
timeout /t 5 >nul
