#!/bin/bash
# Google NotebookLM 전용 세션 1회 로그인 런처
PROFILE_DIR="/Volumes/Realtek_NVME/stock_dashboard/browser_profiles/gemini_account_1"
mkdir -p "$PROFILE_DIR"

echo "========================================================================="
echo "🚀 [Project AGI] Google NotebookLM 전용 1회 로그인 브라우저를 엽니다."
echo "========================================================================="
echo "1. 열리는 Chrome 창에서 Google 계정으로 로그인해 주세요."
echo "2. 로그인 후 https://notebooklm.google.com 메인 화면이 정상적으로 뜨면"
echo "3. 브라우저 창을 닫아주시면 세션이 영구 저장되어 무인 로봇이 작동합니다."
echo "========================================================================="

open -na "Google Chrome" --args --user-data-dir="$PROFILE_DIR" --no-first-run --no-default-browser-check "https://notebooklm.google.com/"
