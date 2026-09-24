#!/usr/bin/env python3
"""
M4 Mac 전용 Gemini Advanced 구독 계정 브라우저 로그인 도우미
Mac 네이티브 Chrome을 독립 프로필 디렉토리와 함께 직접 실행합니다.
별도 라이브러리(playwright 등) 없이도 구글 2차 인증/보안 차단 없이 완벽하게 로그인됩니다.
"""
import sys
import os
import argparse
import subprocess

BASE_DIR = "/Volumes/Realtek_NVME/stock_dashboard"
PROFILE_DIRS = {
    "1": os.path.join(BASE_DIR, "browser_profiles/gemini_account_1"),
    "2": os.path.join(BASE_DIR, "browser_profiles/gemini_account_2"),
}

def open_login_browser(account_num="1"):
    user_data_dir = PROFILE_DIRS.get(str(account_num), PROFILE_DIRS["1"])
    os.makedirs(user_data_dir, exist_ok=True)
    
    print("==================================================")
    print(f"🚀 [Google Chrome] Gemini Advanced 구독 계정 [{account_num}] 로그인 창을 엽니다.")
    print(f"📂 프로필 저장 경로: {user_data_dir}")
    print("👉 화면에 열린 Chrome 브라우저에서 구글 계정으로 로그인해 주세요.")
    print("👉 gemini.google.com 화면이 정상 로딩되면 브라우저 창을 닫아주시면 세션이 자동 보존됩니다.")
    print("==================================================")
    
    # Mac 네이티브 Chrome 실행 명령어 (독립 프로필)
    chrome_cmd = [
        "open", "-na", "Google Chrome", "--args",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "https://gemini.google.com/app"
    ]
    
    try:
        subprocess.run(chrome_cmd, check=True)
        print(f"\n✅ Chrome 브라우저 창이 실행되었습니다. 로그인을 진행해 주세요!")
    except Exception as e:
        print(f"\n❌ Chrome 실행 중 오류 발생: {e}")
        print("💡 수동 실행 명령어:")
        print(f'open -na "Google Chrome" --args --user-data-dir="{user_data_dir}" "https://gemini.google.com/app"')

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gemini Advanced 구독 계정 1회 로그인 런처")
    parser.add_argument("--account", choices=["1", "2"], default="1", help="로그인할 계정 번호 (1 또는 2)")
    args = parser.parse_args()
    open_login_browser(args.account)
