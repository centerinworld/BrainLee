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

# 요청 간격 (초) — ETF/ETN 제외 후에도 3천여 종목이라 1.5초면 90분+ 소요된다.
REQUEST_DELAY = float(os.getenv("ETF_CHECK_REQUEST_DELAY", "0.6"))

# 연속 실패 알림 임계값
CONSECUTIVE_FAIL_THRESHOLD = 5


def _send_telegram(msg: str):
    """기존 notifier.py 활용—안되면 로그만 출력"""
    try:
        sys.path.insert(0, "/Applications/stock_dashboard")
        from notifier import send
        send(msg, key=f"etf_session_{date.today()}")
    except Exception as e:
        logger.warning(f"Telegram 전송 실패: {e}")


def get_stock_list():
    """stock.db의 stock_universe에서 코스피/코스닥 보통주 코드 + 이름 가져오기."""
    conn = sqlite3.connect(f"file:{STOCK_DB}?mode=ro", uri=True, timeout=3)
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
          AND  market IN ('KOSPI', 'KOSDAQ', '유가증권', '코스닥')
          AND  COALESCE(stock_type, '') NOT IN ('ETF', 'ETF/ETN', 'ETN')
          AND  stock_name NOT LIKE '%ETF%'
          AND  stock_name NOT LIKE '%ETN%'
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


def save_batch(records: list, trade_date: str, is_backfilled: int = 0):
    """수집 결과 일괄 DB 저장"""
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()
    cur.executemany("""
        INSERT OR REPLACE INTO etf_inclusion_daily
        (trade_date, stock_code, stock_name, etf_amount, current_price,
         market_cap, mktcap_ratio, etf_count, is_backfilled)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        (trade_date, r["stock_code"], r.get("stock_name"), r.get("etf_amount"),
         r.get("current_price"), r.get("market_cap"), r.get("mktcap_ratio"),
         r.get("etf_count"), is_backfilled)
        for r in records
    ])
    conn.commit()
    conn.close()
    label = " [백필]" if is_backfilled else ""
    logger.info(f"[DB]{label} {len(records)}건 저장 완료 ({trade_date})")


def _save_failures(failed_codes: list, trade_date: str, run_type: str = 'main'):
    """실패 종목을 collection_failures 테이블에 기록"""
    if not failed_codes:
        return
    conn = sqlite3.connect(DB_PATH)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.executemany("""
        INSERT INTO collection_failures (trade_date, stock_code, run_type, resolved, updated_at)
        VALUES (?, ?, ?, 0, ?)
        ON CONFLICT(trade_date, stock_code) DO UPDATE SET
            run_type=excluded.run_type,
            updated_at=excluded.updated_at
    """, [(trade_date, code, run_type, now) for code in failed_codes])
    conn.commit()
    conn.close()
    logger.info(f"[FAILURES] {len(failed_codes)}건 기록 ({trade_date}, {run_type})")


def _mark_failures_resolved(codes: list, trade_date: str, status: int):
    """실패 종목 해결 상태 업데이트 (1=재수집성공, 2=백필처리)"""
    if not codes:
        return
    conn = sqlite3.connect(DB_PATH)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.executemany("""
        UPDATE collection_failures SET resolved=?, updated_at=?
        WHERE trade_date=? AND stock_code=?
    """, [(status, now, trade_date, code) for code in codes])
    conn.commit()
    conn.close()


def _get_unresolved_failures(trade_date: str) -> list:
    """해당 날짜의 미해결 실패 종목 코드 목록 반환"""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT stock_code FROM collection_failures
        WHERE trade_date=? AND resolved=0
    """, (trade_date,)).fetchall()
    conn.close()
    return [r[0] for r in rows]


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
    aborted       = False
    buffer        = []
    failed_codes  = []
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
                    aborted = True
                    break

                if result and isinstance(result, dict) and result.get("etf_amount") is not None:
                    buffer.append(result)
                    success_count += 1
                    logger.info(f"[{i+1}/{total}] {code} {result.get('stock_name','')} — ETF편입: {result.get('etf_amount')}억원")
                else:
                    fail_count += 1
                    failed_codes.append(code)
                    logger.warning(f"[{i+1}/{total}] {code} — 데이터 없음")

            except Exception as e:
                fail_count += 1
                failed_codes.append(code)
                logger.error(f"[{i+1}/{total}] {code} error: {e}")

            # 버퍼 flush
            if len(buffer) >= FLUSH_EVERY:
                save_batch(buffer, trade_date)
                buffer.clear()

        browser.close()

    # 남은 데이터 저장
    if buffer:
        save_batch(buffer, trade_date)

    # 실패 종목 기록
    if failed_codes:
        _save_failures(failed_codes, trade_date, run_type='main')

    # 실행 로그 완료 기록
    conn = sqlite3.connect(DB_PATH)
    status = "done" if (not aborted and success_count > 0 and success_count + fail_count >= total) else "error"
    conn.execute("""
        UPDATE collection_log
        SET finished_at=?, success=?, failed=?, status=?
        WHERE id=?
    """, (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), success_count, fail_count, status, log_id))
    conn.commit()
    conn.close()

    logger.info(f"수집 완료: 성공 {success_count} / 실패 {fail_count} / 전체 {total}")


def run_retry(trade_date: str = None):
    """실패 종목 재수집 (메인 수집 후 ~3시간 뒤 실행)"""
    if trade_date is None:
        trade_date = date.today().strftime("%Y-%m-%d")

    failed_codes = _get_unresolved_failures(trade_date)
    if not failed_codes:
        logger.info(f"[RETRY] 재수집 대상 없음 ({trade_date})")
        return

    logger.info(f"[RETRY] 재수집 시작: {len(failed_codes)}개 종목 / {trade_date}")

    success_codes = []
    still_failed  = []

    with sync_playwright() as p:
        browser, ctx = create_session_context(p)
        page         = ctx.new_page()

        # 세션 확인
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=15000)
        if "signin" in page.url:
            msg = (
                "🔐 [ETF_check 재수집] 세션 만료\n"
                f"날짜: {trade_date}\n"
                "👉 python ETF_check/test_single.py --login"
            )
            _send_telegram(msg)
            logger.error("[RETRY] 세션 만료 — 중단")
            browser.close()
            return

        total = len(failed_codes)
        buffer = []
        FLUSH_EVERY = 20

        for i, code in enumerate(failed_codes):
            try:
                result = collect_one(page, code)

                if result == "SESSION_EXPIRED":
                    _send_telegram(f"🔐 [ETF_check 재수집] 세션 만료 ({i+1}/{total})")
                    logger.error("[RETRY] 세션 만료 — 중단")
                    break

                if result and isinstance(result, dict) and result.get("etf_amount") is not None:
                    buffer.append(result)
                    success_codes.append(code)
                    logger.info(f"[RETRY {i+1}/{total}] {code} — 성공: {result.get('etf_amount')}억원")
                else:
                    still_failed.append(code)
                    logger.warning(f"[RETRY {i+1}/{total}] {code} — 재수집 실패")

            except Exception as e:
                still_failed.append(code)
                logger.error(f"[RETRY {i+1}/{total}] {code} error: {e}")

            if len(buffer) >= FLUSH_EVERY:
                save_batch(buffer, trade_date)
                buffer.clear()

        browser.close()

    if buffer:
        save_batch(buffer, trade_date)

    # 성공 종목 resolved=1 업데이트
    if success_codes:
        _mark_failures_resolved(success_codes, trade_date, status=1)
        logger.info(f"[RETRY] 재수집 성공: {len(success_codes)}개")

    # 재시도에도 실패한 종목은 run_type='retry'로 갱신
    if still_failed:
        _save_failures(still_failed, trade_date, run_type='retry')
        logger.info(f"[RETRY] 재수집 후 여전히 실패: {len(still_failed)}개")

    logger.info(f"[RETRY] 완료: 성공 {len(success_codes)} / 실패 {len(still_failed)} / 전체 {len(failed_codes)}")


def backfill_from_previous_day(trade_date: str = None):
    """재수집 후에도 실패한 종목을 전일 값으로 백필 (is_backfilled=1)"""
    if trade_date is None:
        trade_date = date.today().strftime("%Y-%m-%d")

    failed_codes = _get_unresolved_failures(trade_date)
    if not failed_codes:
        logger.info(f"[BACKFILL] 백필 대상 없음 ({trade_date})")
        return

    logger.info(f"[BACKFILL] 시작: {len(failed_codes)}개 종목 / {trade_date}")

    conn = sqlite3.connect(DB_PATH)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 전일 데이터에서 복사 (is_backfilled=1로 표시)
    placeholders = ",".join(["?"] * len(failed_codes))
    rows = conn.execute(f"""
        SELECT e.stock_code, e.stock_name, e.etf_amount, e.current_price,
               e.market_cap, e.mktcap_ratio, e.etf_count
        FROM etf_inclusion_daily e
        INNER JOIN (
            SELECT stock_code, MAX(trade_date) AS max_date
            FROM etf_inclusion_daily
            WHERE stock_code IN ({placeholders})
              AND trade_date < ?
              AND is_backfilled = 0
              AND etf_amount IS NOT NULL
            GROUP BY stock_code
        ) latest ON e.stock_code = latest.stock_code AND e.trade_date = latest.max_date
    """, failed_codes + [trade_date]).fetchall()

    backfilled_codes = []
    if rows:
        conn.executemany("""
            INSERT OR REPLACE INTO etf_inclusion_daily
            (trade_date, stock_code, stock_name, etf_amount, current_price,
             market_cap, mktcap_ratio, etf_count, is_backfilled, collected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        """, [
            (trade_date, r[0], r[1], r[2], r[3], r[4], r[5], r[6], now)
            for r in rows
        ])
        conn.commit()
        backfilled_codes = [r[0] for r in rows]
        logger.info(f"[BACKFILL] {len(backfilled_codes)}개 전일 값 복사 완료")

    # 전일 데이터조차 없는 종목 (신규 상장 등)
    no_prev_data = set(failed_codes) - set(backfilled_codes)
    if no_prev_data:
        logger.warning(f"[BACKFILL] 전일 데이터 없음 — {len(no_prev_data)}개: {list(no_prev_data)[:10]}")

    conn.close()

    # resolved=2(백필처리)로 마킹
    all_processed = backfilled_codes + list(no_prev_data)
    _mark_failures_resolved(all_processed, trade_date, status=2)

    # 연속 실패 5일 체크 후 알림
    check_consecutive_failures(trade_date)

    logger.info(f"[BACKFILL] 완료: 백필 {len(backfilled_codes)} / 전일없음 {len(no_prev_data)}")


def check_consecutive_failures(trade_date: str = None):
    """5일 연속 수집 실패(재수집 포함) 종목이 있으면 텔레그램 알림"""
    if trade_date is None:
        trade_date = date.today().strftime("%Y-%m-%d")

    conn = sqlite3.connect(DB_PATH)

    # 최근 수집일 최대 THRESHOLD개 가져오기
    recent_dates = conn.execute("""
        SELECT DISTINCT trade_date FROM collection_failures
        WHERE trade_date <= ?
        ORDER BY trade_date DESC
        LIMIT ?
    """, (trade_date, CONSECUTIVE_FAIL_THRESHOLD)).fetchall()
    recent_dates = [r[0] for r in recent_dates]

    if len(recent_dates) < CONSECUTIVE_FAIL_THRESHOLD:
        conn.close()
        return  # 아직 충분한 날짜 데이터 없음

    # 해당 기간 동안 한 번도 성공(resolved=1)하지 못한 종목 찾기
    placeholders = ",".join(["?"] * len(recent_dates))
    rows = conn.execute(f"""
        SELECT stock_code, COUNT(DISTINCT trade_date) AS fail_days
        FROM collection_failures
        WHERE trade_date IN ({placeholders})
          AND resolved != 1
        GROUP BY stock_code
        HAVING fail_days >= ?
        ORDER BY fail_days DESC
    """, recent_dates + [CONSECUTIVE_FAIL_THRESHOLD]).fetchall()
    conn.close()

    if not rows:
        return

    stock_list = "\n".join([f"  - {r[0]} ({r[1]}일 연속)" for r in rows[:20]])
    msg = (
        f"⚠️ [ETF_check] {CONSECUTIVE_FAIL_THRESHOLD}일 연속 수집 실패 종목\n"
        f"기준일: {trade_date}\n"
        f"총 {len(rows)}개 종목:\n"
        f"{stock_list}"
        + (f"\n  ... 외 {len(rows)-20}개" if len(rows) > 20 else "")
    )
    logger.warning(f"[ALERT] 연속 실패 {len(rows)}개 종목 — 텔레그램 알림")
    _send_telegram(msg)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date",     type=str, default=None, help="수집일자 YYYY-MM-DD")
    parser.add_argument("--limit",    type=int, default=None, help="테스트용 종목 수 제한")
    parser.add_argument("--retry",    action="store_true",    help="실패 종목 재수집")
    parser.add_argument("--backfill", action="store_true",    help="전일 값 백필")
    args = parser.parse_args()

    from init_db import init_db
    init_db()

    if args.retry:
        run_retry(trade_date=args.date)
    elif args.backfill:
        backfill_from_previous_day(trade_date=args.date)
    else:
        run_collection(trade_date=args.date, limit=args.limit)
