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
from datetime import date, datetime, timedelta

from playwright.sync_api import sync_playwright, Page

# ── 경로 설정 ──────────────────────────────────────────────────────
DIR        = os.path.dirname(__file__)
DB_PATH    = os.path.join(DIR, "etf_check.db")
STOCK_DB   = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"
LOG_PATH   = os.path.join(DIR, "collector.log")
STATE_PATH = os.path.join(DIR, "session_state.json")

BASE_URL   = "https://www.etfcheck.co.kr"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/139.0.0.0 Safari/537.36"
)

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

# 요청 간격 (초)
# 2026-08-07 기준 etfcheck SPA가 domcontentloaded 직후엔 "0억원 / 0종목" 임시 상태를
# 먼저 그리고 약 2초 뒤 실제 편입금액을 채운다. 0.6초 시점 파싱은 성공처럼 보이면서
# 잘못된 0값을 저장할 수 있어 기본 대기시간을 2초로 상향한다.
REQUEST_DELAY = float(os.getenv("ETF_CHECK_REQUEST_DELAY", "2.5"))

# 연속 실패 알림 임계값
CONSECUTIVE_FAIL_THRESHOLD = 5
RUN_FAIL_FAST_AFTER = int(os.getenv("ETF_CHECK_FAIL_FAST_AFTER", "30"))
ETF_SCOPE_LABEL = os.getenv("ETF_CHECK_SCOPE_LABEL", "K-ETF")


def _scope_class_is_active(class_name: str | None) -> bool:
    return "inactive" not in str(class_name or "")


def _persist_session_state(ctx) -> bool:
    """브라우저 컨텍스트의 최신 쿠키/스토리지를 session_state.json에 다시 저장.

    기존 구현은 최초 수동 로그인 때 저장한 session_state.json을 매일 읽기만 하고,
    정상 수집 중 서버가 회전시킨 쿠키/세션 정보를 파일에 반영하지 않았다.
    그 결과 브라우저 메모리 내 세션은 연장됐더라도 디스크의 세션 파일은 오래된 상태로
    남아 다음 실행에서 다시 만료 세션을 로드할 수 있다.
    """
    try:
        state = ctx.storage_state()
        with open(STATE_PATH, "w", encoding="utf-8") as fp:
            import json

            json.dump(state, fp, ensure_ascii=False, indent=2)
        logger.info(f"[SESSION] session_state 갱신 저장 완료: {STATE_PATH}")
        return True
    except Exception as e:
        logger.warning(f"[SESSION] session_state 저장 실패: {e}")
        return False


def _select_scope_tab(page: Page, label: str = ETF_SCOPE_LABEL) -> bool:
    """원천 사이트의 집계 범위를 명시적으로 고정한다.

    2026-08-14 점검 결과 etfcheck 모바일 상세페이지 기본값이 `전체`이며,
    이 값은 `K-ETF`보다 훨씬 큰 편입금액/검색수를 보여준다.
    과거 수집기는 기본 선택 상태를 신뢰해 그대로 파싱했기 때문에
    사이트 기본 범위가 바뀌면 시계열 전체가 깨질 수 있었다.
    """
    if not label:
        return True
    try:
        scope_toggle = page.get_by_text(label, exact=True).first
        scope_toggle.click(timeout=3000)
        time.sleep(2.0)
        if not _scope_class_is_active(scope_toggle.get_attribute("class")):
            scope_toggle.click(timeout=3000)
            time.sleep(2.0)
        if not _scope_class_is_active(scope_toggle.get_attribute("class")):
            logger.warning(f"[SCOPE] '{label}' 탭이 inactive 상태입니다")
            return False
        return True
    except Exception as e:
        logger.warning(f"[SCOPE] '{label}' 탭 선택 실패: {e}")
        return False


def _send_telegram(msg: str):
    """기존 notifier.py 활용—안되면 로그만 출력"""
    try:
        sys.path.insert(0, "/Volumes/Realtek_NVME/stock_dashboard/runtime")
        from notifier import send
        send(msg, key=f"etf_session_{date.today()}")
    except Exception as e:
        logger.warning(f"Telegram 전송 실패: {e}")


def get_stock_list(offset: int = 0, limit: int | None = None):
    """stock.db의 stock_universe에서 코스피/코스닥 보통주 코드 + 이름 가져오기."""
    conn = sqlite3.connect(f"file:{STOCK_DB}?mode=ro", uri=True, timeout=3)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT stock_code, stock_name, market, secugrp_nm
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
    stocks = [dict(r) for r in rows]
    total = len(stocks)
    if offset or limit is not None:
        start = max(0, int(offset or 0))
        end = None if limit is None else start + int(limit)
        sliced = stocks[start:end]
        logger.info(f"총 {total}개 종목 로드 / 이번 실행 대상 {len(sliced)}개 (offset={start}, limit={limit or 'ALL'})")
        return sliced
    logger.info(f"총 {total}개 종목 로드")
    return stocks


def save_stock_meta(stocks: list) -> None:
    """대형 주가 DB를 화면 조회 때마다 붙이지 않도록 종목 메타를 로컬 캐시한다."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany(
            """
            INSERT INTO etf_stock_meta (stock_code, stock_name, market, secugrp_nm, updated_at)
            VALUES (?, ?, ?, ?, datetime('now','localtime'))
            ON CONFLICT(stock_code) DO UPDATE SET
                stock_name=excluded.stock_name,
                market=excluded.market,
                secugrp_nm=excluded.secugrp_nm,
                updated_at=excluded.updated_at
            """,
            [(s["stock_code"], s["stock_name"], s.get("market"), s.get("secugrp_nm")) for s in stocks],
        )


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
        user_agent=BROWSER_USER_AGENT,
        locale="ko-KR",
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
    except Exception:
        return None


def _extract_summary_fields(page: Page, stock_code: str) -> dict:
    """상세 페이지 상단 요약 블록에서 핵심 수치를 추출한다.

    원천 페이지는 동일 라벨이 하단 ETF 구성표에도 반복된다.
    단순히 "라벨 다음 줄"만 읽으면 하단 표의 값이나 빈 값을 집는 경우가 있어,
    종목코드가 등장하는 상단 블록 근처만 제한해서 읽는다.
    """
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

    def find_value(labels: list[str], pattern: str | None = None) -> str | None:
        for idx, line in enumerate(window):
            if any(label in line for label in labels):
                for candidate in window[idx + 1:idx + 5]:
                    if pattern is None or re.search(pattern, candidate):
                        return candidate
        return None

    return {
        "name": lines[code_index - 1] if code_index > 0 else None,
        "etf_amount": find_value(["ETF 편입금액", "편입금액(추정)", "ETF 편입", "편입금액"], r"[\d,]+"),
        "price": find_value(["현재가"], r"[\d,]+"),
        "mktcap": find_value(["시가총액"], r"[\d,]+"),
        "ratio": find_value(["시총대비"], r"[\d.]+%"),
        "count": find_value(["ETF 검색수"], r"\d+\s*종목"),
    }


def collect_one(page: Page, stock_code: str) -> dict | None:
    """단일 종목 수집"""
    url = f"{BASE_URL}/mobile/searchPdf/{stock_code}"
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        time.sleep(REQUEST_DELAY)
    except Exception as e:
        logger.warning(f"[{stock_code}] Page load error: {e}")
        return None

    if _is_session_expired(page, stock_code):
        return "SESSION_EXPIRED"
    if not _select_scope_tab(page):
        logger.warning(f"[{stock_code}] '{ETF_SCOPE_LABEL}' 범위를 확인하지 못해 저장 제외")
        return None

    try:
        raw = _extract_summary_fields(page, stock_code)

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
        result = {
            "stock_code":    stock_code,
            "stock_name":    raw.get("name"),
            "etf_amount":    to_num(raw.get("etf_amount")),
            "current_price": to_num(raw.get("price")),
            "market_cap":    to_num(raw.get("mktcap")),
            "mktcap_ratio":  to_pct(raw.get("ratio")),
            "etf_count":     int(count_m.group()) if count_m else None,
        }
        amount = result["etf_amount"]
        market_cap = result["market_cap"]
        source_ratio = result["mktcap_ratio"]
        if amount is None or market_cap is None or market_cap <= 0 or source_ratio is None:
            logger.warning(f"[{stock_code}] 필수 수치 누락: {result}")
            return None
        calculated_ratio = amount * 100.0 / market_cap
        if abs(source_ratio - calculated_ratio) > max(0.11, calculated_ratio * 0.10):
            logger.warning(
                f"[{stock_code}] 단위/파싱 불일치: 원천 비율={source_ratio}, 계산 비율={calculated_ratio:.3f}"
            )
            return None
        # 원천 표시의 반올림 오차 대신 동일 단위 필드로 다시 계산해 내부 일관성을 보장한다.
        result["mktcap_ratio"] = round(calculated_ratio, 4)
        return result
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
         market_cap, mktcap_ratio, etf_count, scope_label, is_backfilled)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        (trade_date, r["stock_code"], r.get("stock_name"), r.get("etf_amount"),
         r.get("current_price"), r.get("market_cap"), r.get("mktcap_ratio"),
         r.get("etf_count"), ETF_SCOPE_LABEL, is_backfilled)
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
            resolved=0,
            updated_at=excluded.updated_at
    """, [(trade_date, code, run_type, now) for code in failed_codes])
    conn.commit()
    conn.close()
    logger.info(f"[FAILURES] {len(failed_codes)}건 기록 ({trade_date}, {run_type})")


def _append_unprocessed_failures(failed_codes: list, stocks: list, start: int) -> int:
    """중단 시점 이후의 미시도 종목을 중복 없이 실패 목록에 추가한다."""
    existing = set(failed_codes)
    remaining = [
        stock["stock_code"]
        for stock in stocks[max(0, start):]
        if stock["stock_code"] not in existing
    ]
    failed_codes.extend(remaining)
    return len(remaining)


def _find_missing_scope_codes(stocks: list, existing_codes: set[str]) -> list[str]:
    return [
        stock["stock_code"]
        for stock in stocks
        if stock["stock_code"] not in existing_codes
    ]


def _register_missing_scope_rows(trade_date: str) -> int:
    """재시도 전에 전체 종목과 K-ETF 저장분을 대조해 하드 중단 누락도 복구한다."""
    stocks = get_stock_list()
    with sqlite3.connect(DB_PATH) as conn:
        existing_codes = {
            row[0]
            for row in conn.execute(
                """
                SELECT stock_code
                FROM etf_inclusion_daily
                WHERE trade_date=? AND scope_label=?
                """,
                (trade_date, ETF_SCOPE_LABEL),
            ).fetchall()
        }
    missing_codes = _find_missing_scope_codes(stocks, existing_codes)
    _mark_failures_resolved(list(existing_codes), trade_date, status=1)
    _save_failures(missing_codes, trade_date, run_type="reconcile_missing")
    if missing_codes:
        logger.warning(f"[RECONCILE] {trade_date} K-ETF 누락 {len(missing_codes)}건을 재수집 대상으로 등록")
    return len(missing_codes)


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


def _finalize_collection_log(log_id: int, success_count: int, fail_count: int, status: str):
    """collection_log를 종료 상태로 마감한다."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        UPDATE collection_log
        SET finished_at=?, success=?, failed=?, status=?
        WHERE id=?
    """, (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), success_count, fail_count, status, log_id))
    conn.commit()
    conn.close()


def _interrupt_stale_collection_logs(max_age_hours: int = 6) -> int:
    """이전 프로세스가 남긴 오래된 running 로그를 error로 마감한다."""
    cutoff = (datetime.now() - timedelta(hours=max_age_hours)).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        """
        UPDATE collection_log
        SET finished_at=?, status='error'
        WHERE status='running'
          AND finished_at IS NULL
          AND COALESCE(started_at, '') != ''
          AND started_at < ?
        """,
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), cutoff),
    )
    conn.commit()
    changed = int(cur.rowcount or 0)
    conn.close()
    if changed:
        logger.warning(f"[LOG] stale running collection_log {changed}건을 error로 정리")
    return changed


def _is_session_expired(page: Page, stock_code: str) -> bool:
    """로그인 세션 만료 여부를 URL이 아니라 실제 본문 내용 기준으로 판정한다."""
    try:
        body_text = page.locator("body").inner_text(timeout=5000)
    except Exception:
        body_text = ""

    if "현재가" in body_text and stock_code in body_text:
        return False

    if "로그인" in body_text and "회원가입" in body_text:
        return True

    if "signin" in page.url:
        return True

    if "/mobile/searchPdf/" not in page.url and "현재가" not in body_text:
        return True

    return False


def run_collection(trade_date: str = None, limit: int = None, offset: int = 0):
    """메인 수집 실행"""
    if trade_date is None:
        trade_date = date.today().strftime("%Y-%m-%d")

    _interrupt_stale_collection_logs()

    stocks = get_stock_list(offset=offset, limit=limit)
    save_stock_meta(stocks)

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
    consecutive_fail_count = 0
    FLUSH_EVERY   = 50  # 50건마다 DB에 저장

    with sync_playwright() as p:
        browser, ctx = create_session_context(p)
        page         = ctx.new_page()

        # 세션 확인 — 만료 시 텔레그램 알림 후 종료.
        # 2026-07-29 수정: BASE_URL(루트)은 로그인 여부와 무관하게 항상 공개 접근 가능해
        # 이 체크가 사실상 항상 통과("세션 확인 OK")하는 무의미한 검사였음(실제로 3개월
        # 묵은 세션이 만료된 상태에서도 계속 OK로 잘못 통과되어 30건 연속 조기중단이
        # 반복됨). 실제 인증이 필요한 종목 상세 페이지(005930)로 검증하도록 변경.
        page.goto(f"{BASE_URL}/mobile/searchPdf/005930", wait_until="domcontentloaded", timeout=15000)
        time.sleep(max(REQUEST_DELAY, 2.0))
        if _is_session_expired(page, "005930"):
            msg = (
                "🔐 [ETF_check] 세션 만료\n"
                f"날짜: {trade_date}\n"
                "ETF 편입금액 수집을 시작할 수 없습니다.\n"
                "👉 터미널에서 재로그인 필요:\n"
                "   python ETF_check/test_single.py --login"
            )
            logger.error("세션 만료 — 텔레그램 알림 전송")
            _send_telegram(msg)
            all_codes = [stock["stock_code"] for stock in stocks]
            _save_failures(all_codes, trade_date, run_type="session_expired")
            browser.close()
            _finalize_collection_log(log_id, success_count=0, fail_count=total, status="error")
            return
        logger.info(f"세션 확인 OK — {page.url}")
        _persist_session_state(ctx)
        _select_scope_tab(page)

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
                    fail_count += _append_unprocessed_failures(failed_codes, stocks, i)
                    break

                if result and isinstance(result, dict) and result.get("etf_amount") is not None:
                    buffer.append(result)
                    success_count += 1
                    consecutive_fail_count = 0
                    logger.info(f"[{i+1}/{total}] {code} {result.get('stock_name','')} — ETF편입: {result.get('etf_amount')}억원")
                else:
                    fail_count += 1
                    consecutive_fail_count += 1
                    failed_codes.append(code)
                    logger.warning(f"[{i+1}/{total}] {code} — 데이터 없음")

            except Exception as e:
                fail_count += 1
                consecutive_fail_count += 1
                failed_codes.append(code)
                logger.error(f"[{i+1}/{total}] {code} error: {e}")

            if success_count == 0 and consecutive_fail_count >= RUN_FAIL_FAST_AFTER:
                msg = (
                    "⚠️ [ETF_check] 전면 수집 실패 감지\n"
                    f"날짜: {trade_date}\n"
                    f"연속 실패: {consecutive_fail_count}건\n"
                    "저장된 로그인 세션 또는 etfcheck 페이지 구조를 확인하세요.\n"
                    "👉 python ETF_check/test_single.py --login"
                )
                logger.error("[ETF_check] 성공 0건 상태의 연속 실패 — 조기 중단")
                _send_telegram(msg)
                aborted = True
                fail_count += _append_unprocessed_failures(failed_codes, stocks, i + 1)
                break

            # 버퍼 flush
            if len(buffer) >= FLUSH_EVERY:
                flushed_codes = [record["stock_code"] for record in buffer]
                save_batch(buffer, trade_date)
                _mark_failures_resolved(flushed_codes, trade_date, status=1)
                buffer.clear()
                _persist_session_state(ctx)

        _persist_session_state(ctx)
        browser.close()

    # 남은 데이터 저장
    if buffer:
        flushed_codes = [record["stock_code"] for record in buffer]
        save_batch(buffer, trade_date)
        _mark_failures_resolved(flushed_codes, trade_date, status=1)

    # 실패 종목 기록
    if failed_codes:
        _save_failures(failed_codes, trade_date, run_type='main')

    # 실행 로그 완료 기록
    status = "done" if (not aborted and success_count > 0 and success_count + fail_count >= total) else "error"
    _finalize_collection_log(log_id, success_count=success_count, fail_count=fail_count, status=status)

    logger.info(f"수집 완료: 성공 {success_count} / 실패 {fail_count} / 전체 {total}")


def run_retry(trade_date: str = None):
    """실패 종목 재수집 (메인 수집 후 ~3시간 뒤 실행)"""
    if trade_date is None:
        trade_date = date.today().strftime("%Y-%m-%d")

    _register_missing_scope_rows(trade_date)
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

        # 세션 확인 (2026-07-29: 실제 인증필요 페이지로 검증, 위 사유와 동일)
        page.goto(f"{BASE_URL}/mobile/searchPdf/005930", wait_until="domcontentloaded", timeout=15000)
        time.sleep(max(REQUEST_DELAY, 2.0))
        if _is_session_expired(page, "005930"):
            msg = (
                "🔐 [ETF_check 재수집] 세션 만료\n"
                f"날짜: {trade_date}\n"
                "👉 python ETF_check/test_single.py --login"
            )
            _send_telegram(msg)
            logger.error("[RETRY] 세션 만료 — 중단")
            browser.close()
            return
        _persist_session_state(ctx)
        _select_scope_tab(page)

        total = len(failed_codes)
        buffer = []
        consecutive_fail_count = 0
        FLUSH_EVERY = 20

        for i, code in enumerate(failed_codes):
            try:
                result = collect_one(page, code)

                if result == "SESSION_EXPIRED":
                    _send_telegram(f"🔐 [ETF_check 재수집] 세션 만료 ({i+1}/{total})")
                    logger.error("[RETRY] 세션 만료 — 중단")
                    still_failed.extend(failed_codes[i:])
                    break

                if result and isinstance(result, dict) and result.get("etf_amount") is not None:
                    buffer.append(result)
                    success_codes.append(code)
                    consecutive_fail_count = 0
                    logger.info(f"[RETRY {i+1}/{total}] {code} — 성공: {result.get('etf_amount')}억원")
                else:
                    still_failed.append(code)
                    consecutive_fail_count += 1
                    logger.warning(f"[RETRY {i+1}/{total}] {code} — 재수집 실패")

            except Exception as e:
                still_failed.append(code)
                consecutive_fail_count += 1
                logger.error(f"[RETRY {i+1}/{total}] {code} error: {e}")

            if not success_codes and consecutive_fail_count >= RUN_FAIL_FAST_AFTER:
                _send_telegram(
                    "⚠️ [ETF_check 재수집] 전면 실패 감지\n"
                    f"날짜: {trade_date}\n"
                    f"연속 실패: {consecutive_fail_count}건\n"
                    "👉 python ETF_check/test_single.py --login"
                )
                logger.error("[RETRY] 성공 0건 상태의 연속 실패 — 조기 중단")
                still_failed.extend(failed_codes[i + 1:])
                break

            if len(buffer) >= FLUSH_EVERY:
                flushed_codes = [record["stock_code"] for record in buffer]
                save_batch(buffer, trade_date)
                _mark_failures_resolved(flushed_codes, trade_date, status=1)
                buffer.clear()
                _persist_session_state(ctx)

        _persist_session_state(ctx)
        browser.close()

    if buffer:
        flushed_codes = [record["stock_code"] for record in buffer]
        save_batch(buffer, trade_date)
        _mark_failures_resolved(flushed_codes, trade_date, status=1)

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
             market_cap, mktcap_ratio, etf_count, scope_label, is_backfilled, collected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        """, [
            (trade_date, r[0], r[1], r[2], r[3], r[4], r[5], r[6], ETF_SCOPE_LABEL, now)
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
    parser.add_argument("--offset",   type=int, default=0, help="시총순 정렬 기준 시작 오프셋")
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
        run_collection(trade_date=args.date, limit=args.limit, offset=args.offset)
