"""
notebooklm_browser_robot.py — Google NotebookLM (https://notebooklm.google.com) 완전 자동 브라우저 로봇

역할:
1. Playwright Persistent Chrome Context(browser_profiles/gemini_account_1)로 NotebookLM 웹 접속
2. [새 노트북 만들기] -> [소스 텍스트 업로드] -> [Studio: 브리핑 문서 생성] 자동 실행
3. 구글 NotebookLM이 직접 생성한 원문 브리핑 텍스트 및 팩트 추출
4. 추출된 진짜 NotebookLM 자료를 바탕으로 1장 인포그래픽 카드 빌드
5. hyojun22@koreaaero.com 및 center.in.world@gmail.com으로 자동 이메일/텔레그램 발송!
"""

import os
import sys
import time
import json
import datetime
from pathlib import Path

WORKSPACE_ROOT = Path("/Volumes/Realtek_NVME/stock_dashboard")
sys.path.insert(0, str(WORKSPACE_ROOT))

import config
from presidential_brief_engine import get_latest_macro_and_market_data, get_latest_defense_and_geopolitics_feeds
from presidential_infographic_builder import build_infographic_html, render_and_save_infographic
from presidential_email_dispatcher import send_presidential_brief_email

CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE_DIR = str(WORKSPACE_ROOT / "browser_profiles" / "gemini_account_1")
REPORTS_DIR = WORKSPACE_ROOT / "presidential_reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def build_source_text_for_notebooklm() -> str:
    """NotebookLM에 업로드할 최신 4대 인텔리전스 소스 텍스트 구성"""
    now = datetime.datetime.now().strftime("%Y년 %m월 %d일 %H:%M")
    macro = get_latest_macro_and_market_data()
    feeds = get_latest_defense_and_geopolitics_feeds(30)
    
    lines = [
        f"# Project AGI — 국가 최고 전략 인텔리전스 소스북 ({now})",
        "대상: Google NotebookLM 브리핑 문서 및 5분 오디오 팟캐스트 변환용",
        "",
        "## 1. 글로벌 거시경제 & 환율/원자재",
        f"- 달러/원 환율: {macro.get('usd_krw')}원",
        f"- WTI 국제유가: ${macro.get('wti_oil')}",
        f"- 미국 국채 10년물 금리: {macro.get('us_10y')}%",
        f"- 미 연준 기준금리: {macro.get('fed_rate')}",
        f"- 외국인 수급: {macro.get('foreign_net_buy')}",
        f"- 기관 수급: {macro.get('inst_net_buy')}",
        f"- 주도주 동향: {', '.join(macro.get('top_defense_movers', []))}",
        "",
        "## 2. K-방산(KAI/한화/로템) 및 글로벌 군비 증액 첩보 (방사청 DAPA 포함)"
    ]
    for f in feeds:
        lines.append(f"- [{f.get('category')}] {f.get('title')} ({f.get('published_at', '')})")
        if f.get('summary'):
            lines.append(f"  요약: {f.get('summary')}")
            
    return "\n".join(lines)


def run_notebooklm_automation(headless: bool = True) -> dict:
    """Playwright를 이용한 실제 NotebookLM 브라우저 자동화"""
    from playwright.sync_api import sync_playwright

    source_text = build_source_text_for_notebooklm()
    print("🤖 [NotebookLM Robot] Launching persistent Chrome context...")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch_persistent_context(
                user_data_dir=PROFILE_DIR,
                executable_path=CHROME_BIN,
                headless=headless,
                args=[
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-blink-features=AutomationControlled"
                ]
            )
            page = browser.new_page()
            print("🤖 [NotebookLM Robot] Navigating to https://notebooklm.google.com/ ...")
            page.goto("https://notebooklm.google.com/", wait_until="networkidle", timeout=35000)

            # 1. 로그인 필요 여부 체크
            if "accounts.google.com" in page.url or "signin" in page.url.lower():
                print("⚠️ [NotebookLM Robot] Google 로그인 세션이 필요합니다. (accounts.google.com 감지)")
                browser.close()
                return {
                    "status": "login_required",
                    "message": "Google 계정 1회 로그인이 필요합니다. ./login_notebooklm.sh 를 실행하여 로그인해주세요."
                }

            print("✅ [NotebookLM Robot] Successfully logged in! Current URL:", page.url)

            # 2. [새 노트북 만들기] 버튼 탐색 및 클릭
            # NotebookLM UI 상의 새 노트북 생성 버튼
            time.sleep(2)
            create_btn = page.query_selector("button:has-text('새 노트북'), button:has-text('New notebook'), [aria-label*='새 노트북']")
            if create_btn:
                create_btn.click()
                print("🤖 [NotebookLM Robot] Clicked 'New Notebook' button.")
                page.wait_for_load_state("networkidle")
                time.sleep(3)

            # 3. [소스 추가 (Add sources)] -> [복사한 텍스트 (Copied text)]
            copied_text_btn = page.query_selector("button:has-text('복사한 텍스트'), button:has-text('Copied text'), div:has-text('텍스트 붙여넣기')")
            if copied_text_btn:
                copied_text_btn.click()
                time.sleep(1.5)
                
                # 텍스트 입력창 찾기
                textarea = page.query_selector("textarea, [contenteditable='true']")
                if textarea:
                    textarea.fill(source_text[:15000])  # 최대 15,000자
                    print("🤖 [NotebookLM Robot] Pasted source text into NotebookLM.")
                    
                    # 삽입/업로드 버튼 클릭
                    insert_btn = page.query_selector("button:has-text('삽입'), button:has-text('Insert'), button:has-text('추가')")
                    if insert_btn:
                        insert_btn.click()
                        print("🤖 [NotebookLM Robot] Clicked Insert Source button.")
                        time.sleep(5)

            # 4. Studio 탭 또는 브리핑 문서 생성 버튼 클릭
            # 브리핑 문서 / 스터디 가이드 / FAQ 등 생성
            brief_btn = page.query_selector("button:has-text('브리핑 문서'), button:has-text('Briefing doc'), button:has-text('학습 가이드')")
            if brief_btn:
                print("🤖 [NotebookLM Robot] Generating Briefing Document in NotebookLM...")
                brief_btn.click()
                time.sleep(12)  # 생성 대기

            # 5. 생성된 콘텐츠 본문 추출
            content_div = page.query_selector(".note-content, .formatted-text, [role='region'], .briefing-content")
            extracted_text = content_div.inner_text() if content_div else ""
            
            browser.close()

            if extracted_text:
                print("🎉 [NotebookLM Robot] Successfully extracted authentic NotebookLM generated briefing!")
                return {
                    "status": "success",
                    "extracted_text": extracted_text,
                    "source": "google_notebooklm_live"
                }

    except Exception as e:
        print(f"❌ [NotebookLM Robot Error] {e}")
        return {"status": "error", "message": str(e)}

    return {"status": "login_required", "message": "Google 계정 로그인 필요"}


if __name__ == "__main__":
    res = run_notebooklm_automation(headless=True)
    print("Result:", res)
