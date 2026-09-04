"""
ETF_check 단일 종목 테스트 스크립트
사용법:
  # 최초 1회 로그인 (브라우저 창 열림 → 수동으로 구글 로그인)
  python test_single.py --login

  # 테스트 (로그인 후)
  python test_single.py --code 000660

  # DB 저장 없이 테스트
  python test_single.py --code 000660 --no-save
"""
import sys
import os
import re
import time
import json
import sqlite3
import argparse
from datetime import date
from pathlib import Path

from playwright.sync_api import sync_playwright

DIR       = Path(__file__).parent
DB_PATH   = DIR / "etf_check.db"
STATE_PATH = DIR / "session_state.json"  # 로그인 세션 저장 파일

BASE_URL  = "https://www.etfcheck.co.kr"
LOGIN_URL = f"{BASE_URL}/mobile/user/signin"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/139.0.0.0 Safari/537.36"
)
ETF_SCOPE_LABEL = os.getenv("ETF_CHECK_SCOPE_LABEL", "K-ETF")


# ─────────────────────────────────────────────────────────────────
# 로그인 & 세션 관리
# ─────────────────────────────────────────────────────────────────

def do_manual_login():
    """
    브라우저 창을 열어 수동으로 구글 로그인.
    로그인 완료 후 Enter를 누르면 세션을 session_state.json에 저장.
    """
    print("\n[INFO] 브라우저 창이 열립니다. 구글 로그인을 직접 완료해 주세요.")
    print("[INFO] 로그인 완료 후 이 터미널에서 Enter를 누르세요.\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx     = browser.new_context(viewport={"width": 1280, "height": 900})
        page    = ctx.new_page()

        page.goto(LOGIN_URL, wait_until="domcontentloaded")

        # 사용자가 수동으로 로그인할 때까지 대기
        input(">>> 로그인 완료 후 Enter 누르기: ")

        # 세션 상태 저장 (쿠키 + localStorage)
        state = ctx.storage_state()
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        print(f"[OK] 세션 저장 완료: {STATE_PATH}")

        browser.close()


def create_context_with_session(playwright):
    """저장된 세션으로 컨텍스트 생성. 없으면 에러 안내."""
    if not STATE_PATH.exists():
        print("[ERROR] 저장된 세션이 없습니다.")
        print("[TIP]  먼저 아래 명령어로 로그인하세요:")
        print("         python test_single.py --login\n")
        sys.exit(1)

    browser = playwright.chromium.launch(headless=True)
    ctx     = browser.new_context(
        viewport={"width": 1280, "height": 900},
        storage_state=str(STATE_PATH),   # 저장된 쿠키/세션 로드
        user_agent=BROWSER_USER_AGENT,
        locale="ko-KR",
    )
    return browser, ctx


def select_scope_tab(page, label: str = ETF_SCOPE_LABEL):
    if not label:
        return
    try:
        page.get_by_text(label, exact=True).first.click(timeout=3000)
        time.sleep(1.2)
    except Exception as e:
        print(f"[WARN] 스코프 탭 선택 실패 ({label}): {e}")


def extract_summary_fields(page, stock_code: str) -> dict:
    body_text = page.locator("body").inner_text()
    lines = [line.strip() for line in body_text.splitlines() if line.strip()]

    code_index = 0
    for idx, line in enumerate(lines):
        if line == stock_code:
            code_index = idx
            break
    else:
        for idx, line in enumerate(lines):
            if re.fullmatch(r"\d{6}", line):
                code_index = idx
                break

    window_end = min(len(lines), code_index + 20)
    window = lines[code_index:window_end]

    def find_value(labels: list[str], pattern: str | None = None):
        for idx, line in enumerate(window):
            if any(label in line for label in labels):
                for candidate in window[idx + 1:idx + 5]:
                    if pattern is None or re.search(pattern, candidate):
                        return candidate
        return None

    return {
        "debug_lines": lines[:30],
        "name": lines[code_index - 1] if code_index > 0 else None,
        "etf_amount": find_value(["ETF 편입금액", "편입금액(추정)", "ETF 편입", "편입금액"], r"[\d,]+"),
        "price": find_value(["현재가"], r"[\d,]+"),
        "mktcap": find_value(["시가총액"], r"[\d,]+"),
        "ratio": find_value(["시총대비"], r"[\d.]+%"),
        "count": find_value(["ETF 검색수"], r"\d+\s*종목"),
    }


# ─────────────────────────────────────────────────────────────────
# 데이터 수집
# ─────────────────────────────────────────────────────────────────

def get_stock_info(page, stock_code: str) -> dict:
    """etfcheck.co.kr에서 종목 ETF 편입금액(추정) 등 수집"""
    url = f"{BASE_URL}/mobile/searchPdf/{stock_code}"
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    time.sleep(2.5)  # SPA 데이터 로드 대기

    # 세션 만료 체크
    if "signin" in page.url:
        return {"error": "SESSION_EXPIRED", "stock_code": stock_code}

    select_scope_tab(page)

    result = {
        "stock_code":    stock_code,
        "stock_name":    None,
        "etf_amount":    None,
        "current_price": None,
        "market_cap":    None,
        "mktcap_ratio":  None,
        "etf_count":     None,
    }

    try:
        raw = extract_summary_fields(page, stock_code)

        print(f"[DEBUG] 상위 30줄:\n{chr(10).join(raw.get('debug_lines', []))}\n")
        print(f"[DEBUG] 추출값: etf={raw.get('etf_amount')} | price={raw.get('price')} | mktcap={raw.get('mktcap')} | ratio={raw.get('ratio')}")

        def to_num(text):
            """'1,286,000 -0.54%' → 1286000.0 (첫 번째 숫자만 추출)"""
            if not text:
                return None
            # 첫 번째 연속된 숫자+쉼표+점 패턴만 추출
            m = re.match(r'^[\s\-]*([\d,]+(?:\.\d+)?)', str(text).replace(',', ''))
            if not m:
                # 쉼표 유지 버전으로 재시도
                m = re.search(r'([\d,]+)', str(text))
                if not m:
                    return None
            cleaned = m.group(1).replace(',', '')
            try:
                return float(cleaned) if cleaned else None
            except:
                return None

        def to_pct(text):
            if not text:
                return None
            m = re.search(r'[\d.]+', str(text))
            return float(m.group()) if m else None

        result["etf_amount"]    = to_num(raw.get("etf_amount"))
        result["current_price"] = to_num(raw.get("price"))
        result["market_cap"]    = to_num(raw.get("mktcap"))
        result["mktcap_ratio"]  = to_pct(raw.get("ratio"))
        result["stock_name"]    = raw.get("name") or stock_code

        count_m = re.search(r'\d+', raw.get("count", "") or "")
        result["etf_count"] = int(count_m.group()) if count_m else None

    except Exception as e:
        print(f"[WARN] JS error for {stock_code}: {e}")

    return result


# ─────────────────────────────────────────────────────────────────
# DB 저장
# ─────────────────────────────────────────────────────────────────

def save_to_db(data: dict, trade_date: str):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        INSERT OR REPLACE INTO etf_inclusion_daily
        (trade_date, stock_code, stock_name, etf_amount,
         current_price, market_cap, mktcap_ratio, etf_count, scope_label)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        trade_date, data["stock_code"], data.get("stock_name"),
        data.get("etf_amount"), data.get("current_price"),
        data.get("market_cap"), data.get("mktcap_ratio"),
        data.get("etf_count"), ETF_SCOPE_LABEL,
    ))
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────
# 메인 테스트
# ─────────────────────────────────────────────────────────────────

def test_single(stock_code: str, save: bool = True):
    print(f"\n{'='*50}")
    print(f"[TEST] 종목코드: {stock_code}")
    print(f"{'='*50}")

    with sync_playwright() as p:
        browser, ctx = create_context_with_session(p)
        page = ctx.new_page()

        result = get_stock_info(page, stock_code)

        # 세션 만료 처리
        if result.get("error") == "SESSION_EXPIRED":
            print("[WARN] 세션 만료됨. 다시 로그인하세요: python test_single.py --login")
            browser.close()
            return None

        browser.close()

    # 결과 출력
    print(f"\n{'='*50}")
    print(f"[결과]")
    print(f"  종목코드          : {result['stock_code']}")
    print(f"  종목명            : {result['stock_name']}")
    print(f"  ETF 편입금액(추정) : {result['etf_amount']} 억원")
    print(f"  현재가            : {result['current_price']}")
    print(f"  시가총액           : {result['market_cap']} 억원")
    print(f"  시총대비           : {result['mktcap_ratio']} %")
    print(f"  ETF 검색수        : {result['etf_count']}")
    print(f"{'='*50}\n")

    if save:
        if result.get("etf_amount") is not None:
            today = date.today().strftime("%Y-%m-%d")
            save_to_db(result, today)
            print(f"[OK] DB 저장 완료 ({today})")
        else:
            print("[WARN] ETF 편입금액 없음 — DB 저장 안 함")

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ETF Check 단일 종목 테스트")
    parser.add_argument("--login",   action="store_true", help="최초 1회 수동 로그인 & 세션 저장")
    parser.add_argument("--code",    type=str, default="000660", help="종목코드 (기본: SK하이닉스)")
    parser.add_argument("--no-save", action="store_true", help="DB 저장 안 함")
    args = parser.parse_args()

    if args.login:
        do_manual_login()
        sys.exit(0)

    # DB 초기화 (없으면 생성)
    from init_db import init_db
    init_db()

    test_single(args.code, save=not args.no_save)
