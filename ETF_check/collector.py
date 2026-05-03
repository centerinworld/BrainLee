"""
ETF_check 전체 종목 수집기
- stock.db의 stock_universe에서 전체 종목 목록을 가져와 etfcheck.co.kr에서 ETF 편입금액 수집
- 수집 결과를 etf_check.db의 etf_inclusion_daily 테이블에 저장
- 실행: python collector.py
"""
import os
import re
import sys
import time
import sqlite3
import logging
from datetime import date, datetime

from playwright.sync_api import sync_playwright, Page

# ── 경로 설정 ──────────────────────────────────────────────────────
DIR        = os.path.dirname(__file__)
DB_PATH    = os.path.join(DIR, "etf_check.db")
STOCK_DB   = "/Applications/stock_dashboard/stock.db"
LOG_PATH   = os.path.join(DIR, "collector.log")
STATE_PATH = os.path.join(DIR, "session_state.json")

BASE_URL   = "https://www.etfcheck.co.kr"

# ── 로깅 설정 ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 요청 간격 (초) — 서버 부하 방지
REQUEST_DELAY = 1.5


def _send_telegram(msg: str):
    """기존 notifier.py 활용—안되면 로그만 출력"""
    try:
        sys.path.insert(0, "/Applications/stock_dashboard")
        from notifier import send
        send(msg, key=f"etf_session_{date.today()}")
    except Exception as e:
        logger.warning(f"Telegram 전송 실패: {e}")


def get_stock_list():
    """stock.db의 stock_universe에서 전체 종목 코드 + 이름 가져오기 (코스피/코스닥만)"""
    conn = sqlite3.connect(STOCK_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT stock_code, stock_name
        FROM   stock_universe
        WHERE  stock_code NOT LIKE '%^%'
          AND  stock_code NOT LIKE 'GC%'
          AND  stock_code NOT LIKE 'CL%'
          AND  stock_code NOT LIKE '%-F'
          AND  stock_code NOT LIKE '%=%'
          AND  stock_code NOT LIKE 'NQ%'
          AND  stock_code NOT LIKE 'ES%'
          AND  length(stock_code) = 6
          AND  stock_code GLOB '[0-9]*'
        ORDER  BY market_cap DESC NULLS LAST
    """).fetchall()
    conn.close()
    logger.info(f"총 {len(rows)}개 종목 로드")
    return [dict(r) for r in rows]


def create_session_context(playwright):
    """session_state.json(저장된 쿠키)으로 컨텍스트 생성"""
    if not os.path.exists(STATE_PATH):
        logger.error(f"세션 파일 없음: {STATE_PATH}")
        logger.error("먼저 로그인하세요: python test_single.py --login")
        raise FileNotFoundError(STATE_PATH)
    browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
    ctx = browser.new_context(
        viewport={"width": 1280, "height": 900},
        storage_state=STATE_PATH,
    )
    return browser, ctx


def parse_number(text: str):
    """'670,014억원' → 670014.0, '7.31%' → 7.31"""
    if not text:
        return None
    # 억원 단위
    cleaned = text.replace(',', '').replace('억원', '').replace('원', '').replace('%', '').strip()
    try:
        return float(cleaned)
    except:
        return None


def collect_one(page: Page, stock_code: str) -> dict | None:
    """단일 종목 수집"""
    url = f"{BASE_URL}/mobile/searchPdf/{stock_code}"
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        time.sleep(REQUEST_DELAY)
    except Exception as e:
        logger.warning(f"[{stock_code}] Page load error: {e}")
        return None

    if "signin" in page.url:
        return "SESSION_EXPIRED"

    try:
        raw = page.evaluate("""
        () => {
            const lines = document.body.innerText.split('\\n').map(l=>l.trim()).filter(l=>l);
            const res = {};
            for (let i=0; i<lines.length; i++) {
                const l = lines[i];
                if (l.includes('편입금액') && !res.etf_amount)  res.etf_amount = lines[i+1];
                if (l.includes('현재가')   && !res.price)       res.price      = lines[i+1];
                if (l.includes('시가총액') && !res.mktcap)      res.mktcap     = lines[i+1];
                if (l.includes('시총대비') && !res.ratio)       res.ratio      = lines[i+1];
                if (l.includes('ETF 검색수') && !res.count)     res.count      = lines[i+1];
            }
            // 종목명: 6자리 코드 바로 위 줄
            for (let i=1; i<lines.length; i++) {
                if (/^\\d{6}$/.test(lines[i])) { res.name = lines[i-1]; break; }
            }
            return res;
        }
        """)

        def to_num(text):
            """'1,286,000 -0.54%' → 1286000.0 (첫 번째 숫자만)"""
            if not text: return None
            m = re.search(r'([\d,]+)', str(text))
            if not m: return None
            try: return float(m.group(1).replace(',', ''))
            except: return None

        def to_pct(text):
            if not text: return None
            m = re.search(r'[\d.]+', str(text))
            return float(m.group()) if m else None

        count_m = re.search(r'\d+', raw.get("count", "") or "")
        return {
            "stock_code":    stock_code,
            "stock_name":    raw.get("name"),
            "etf_amount":    to_num(raw.get("etf_amount")),
            "current_price": to_num(raw.get("price")),
            "market_cap":    to_num(raw.get("mktcap")),
            "mktcap_ratio":  to_pct(raw.get("ratio")),
            "etf_count":     int(count_m.group()) if count_m else None,
        }
    except Exception as e:
        logger.warning(f"[{stock_code}] Parse error: {e}")
        return None


def save_batch(records: list, trade_date: str):
    """수집 결과 일괄 DB 저장"""
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()
    cur.executemany("""
        INSERT OR REPLACE INTO etf_inclusion_daily
        (trade_date, stock_code, stock_name, etf_amount, current_price, market_cap, mktcap_ratio, etf_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        (trade_date, r["stock_code"], r.get("stock_name"), r.get("etf_amount"),
         r.get("current_price"), r.get("market_cap"), r.get("mktcap_ratio"), r.get("etf_count"))
        for r in records
    ])
    conn.commit()
    conn.close()
    logger.info(f"[DB] {len(records)}건 저장 완료 ({trade_date})")


def run_collection(trade_date: str = None, limit: int = None):
    """메인 수집 실행"""
    if trade_date is None:
        trade_date = date.today().strftime("%Y-%m-%d")

    stocks = get_stock_list()
    if limit:
        stocks = stocks[:limit]

    total = len(stocks)
    logger.info(f"수집 시작: {total}개 종목 / {trade_date}")

    # 실행 로그 시작
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO collection_log (run_date, started_at, total_stocks, status)
        VALUES (?, ?, ?, 'running')
    """, (trade_date, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), total))
    conn.commit()
    log_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    success_count = 0
    fail_count    = 0
    buffer        = []
    FLUSH_EVERY   = 50  # 50건마다 DB에 저장

    with sync_playwright() as p:
        browser, ctx = create_session_context(p)
        page         = ctx.new_page()

        # 세션 확인 — 만료 시 텔레그램 알림 후 종료
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=15000)
        if "signin" in page.url:
            msg = (
                "🔐 [ETF_check] 세션 만료\n"
                f"날짜: {trade_date}\n"
                "ETF 편입금액 수집을 시작할 수 없습니다.\n"
                "👉 터미널에서 재로그인 필요:\n"
                "   python ETF_check/test_single.py --login"
            )
            logger.error("세션 만료 — 텔레그램 알림 전송")
            _send_telegram(msg)
            browser.close()
            return
        logger.info(f"세션 확인 OK — {page.url}")

        for i, stock in enumerate(stocks):
            code = stock["stock_code"]
            try:
                result = collect_one(page, code)

                # 세션 만료 시 텔레그램 알림 후 중단
                if result == "SESSION_EXPIRED":
                    msg = (
                        f"🔐 [ETF_check] 수집 중 세션 만료 ({i+1}/{total})\n"
                        f"날짜: {trade_date}\n"
                        "👉 python ETF_check/test_single.py --login"
                    )
                    _send_telegram(msg)
                    logger.error("수집 중 세션 만료 — 중단")
                    break

                if result and isinstance(result, dict) and result.get("etf_amount") is not None:
                    buffer.append(result)
                    success_count += 1
                    logger.info(f"[{i+1}/{total}] {code} {result.get('stock_name','')} — ETF편입: {result.get('etf_amount')}억원")
                else:
                    fail_count += 1
                    logger.warning(f"[{i+1}/{total}] {code} — 데이터 없음")

            except Exception as e:
                fail_count += 1
                logger.error(f"[{i+1}/{total}] {code} error: {e}")

            # 버퍼 flush
            if len(buffer) >= FLUSH_EVERY:
                save_batch(buffer, trade_date)
                buffer.clear()

        browser.close()

    # 남은 데이터 저장
    if buffer:
        save_batch(buffer, trade_date)

    # 실행 로그 완료 기록
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        UPDATE collection_log
        SET finished_at=?, success=?, failed=?, status='done'
        WHERE id=?
    """, (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), success_count, fail_count, log_id))
    conn.commit()
    conn.close()

    logger.info(f"수집 완료: 성공 {success_count} / 실패 {fail_count} / 전체 {total}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date",  type=str, default=None, help="수집일자 YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=None, help="테스트용 종목 수 제한")
    args = parser.parse_args()

    from init_db import init_db
    init_db()
    run_collection(trade_date=args.date, limit=args.limit)
