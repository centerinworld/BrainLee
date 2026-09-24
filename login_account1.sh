#!/bin/bash
# Gemini Advanced 구독 계정 1 로그인 런처
PROFILE_DIR="/Volumes/Realtek_NVME/stock_dashboard/browser_profiles/gemini_account_1"
mkdir -p "$PROFILE_DIR"
echo "🚀 [계정 1] Gemini Advanced 전용 Chrome 창을 실행합니다..."
open -na "Google Chrome" --args --user-data-dir="$PROFILE_DIR" --no-first-run --no-default-browser-check "https://gemini.google.com/app"
echo "✅ 로그인 완료 후 브라우저 창을 닫으시면 세션이 영구 저장됩니다."
