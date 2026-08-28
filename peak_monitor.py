"""
peak_monitor.py — 스탁이지 3전략 모니터 (즉시 알림 버전)

기능:
  · Peak Easy / 모멘텀 Easy / 벨류 Easy 동시 모니터링
  · 신규 편입 감지 즉시 텔레그램 발송 (감지 지연 최소화)
  · 이탈 종목 감지 즉시 텔레그램 발송
  · 가상매매 DB 기록

폴링 주기 (장 상태별 자동 조정):
  · 09:00~15:00 (장중)   : 3분 간격
  · 15:00~16:30 (장마감)  : 1분 간격  ← 스탁이지 업데이트 직후 즉시 감지
  · 16:30~22:00 (야간)    : 5분 간격
  · 22:00~09:00 (심야)    : 10분 간격
  · 주말                  : 30분 간격
  3개 전략은 ThreadPoolExecutor로 병렬 실행
"""

import time
import threading
import requests
import logging
import logging.handlers
import json
import sys
import atexit
import os
import re
import httpx
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta
from bs4 import BeautifulSoup

sys.path.insert(0, "/Applications/stock_dashboard")
from notifier import send as send_telegram, load_history as _load_sent_history, purge_old_keys as _clear_old_sent_keys

# ──────────────────────────────────────────────────────────────
# 로거
# ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            "/Applications/stock_dashboard/peak_monitor.log",
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=3,
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# ──────────────────────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────────────────────
import config

STOCKEASY_EMAIL    = getattr(config, "STOCKEASY_EMAIL",    "")
STOCKEASY_PASSWORD = getattr(config, "STOCKEASY_PASSWORD", "")
BASE_API           = "http://127.0.0.1:8000"
MAX_BUDGET         = 10_000_000   # 가상매매 한도 1,000만원
API_TIMEOUT_DEFAULT = 6
API_TIMEOUT_UPDATE = 2
API_UPDATE_MAX_PENDING = 6
_API_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="peak-api")
_PENDING_UPDATE_POSTS: set = set()
atexit.register(lambda: _API_EXECUTOR.shutdown(wait=False, cancel_futures=True))

# ── 폴링 주기 (초) ──────────────────────────────────────────
INTERVAL_MARKET_CLOSE = 60    # 15:00~16:30 — 장마감 집중 감시 (1분)
INTERVAL_MARKET_OPEN  = 180   # 09:00~15:00 — 장중 (3분)
INTERVAL_EVENING      = 300   # 16:30~22:00 — 야간 (5분)
INTERVAL_NIGHT        = 600   # 22:00~09:00 — 심야 (10분)
INTERVAL_WEEKEND      = 1800  # 주말 (30분)

# ── 전략별 설정 ─────────────────────────────────────────────
STRATEGY_URLS = {
    "peak":     "https://stockeasy.intellio.kr/strategy-room/peak",
    "momentum": "https://stockeasy.intellio.kr/strategy-room/momentum",
    "value":    "https://stockeasy.intellio.kr/strategy-room/value",
}
STRATEGY_NAMES = {
    "peak":     "Peak Easy",
    "momentum": "모멘텀 Easy",
    "value":    "벨류 Easy",
}

LOGIN_URL    = "https://stockeasy.intellio.kr/api/auth/login"
SESSION_FILE = "/Applications/stock_dashboard/stockeasy_session.json"

# 편출 오탐 방지: 1회 누락으로 즉시 편출 확정하지 않음
_EXIT_MISS_COUNTS: dict[tuple[str, str], int] = {}
EXIT_MISS_CONFIRM_COUNT = 2


# ──────────────────────────────────────────────────────────────
# 폴링 주기 계산
# ──────────────────────────────────────────────────────────────
def get_poll_interval() -> int:
    """현재 시각에 따라 최적 폴링 주기(초) 반환."""
    now = datetime.now()
    if now.weekday() >= 5:
        return INTERVAL_WEEKEND

    h, m = now.hour, now.minute
    t = h * 60 + m

    market_close_start = 15 * 60        # 15:00
    market_close_end   = 16 * 60 + 30   # 16:30
    market_open_start  = 9  * 60        # 09:00
    evening_end        = 22 * 60        # 22:00

    if market_close_start <= t < market_close_end:
        return INTERVAL_MARKET_CLOSE   # 장마감 집중: 1분
    elif market_open_start <= t < market_close_start:
        return INTERVAL_MARKET_OPEN    # 장중: 3분
    elif market_close_end <= t < evening_end:
        return INTERVAL_EVENING        # 야간: 5분
    else:
        return INTERVAL_NIGHT          # 심야: 10분


# ──────────────────────────────────────────────────────────────
# 스탁이지 세션
# ──────────────────────────────────────────────────────────────
class StockeasySession:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent":     "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept":         "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language":"ko-KR,ko;q=0.9",
            "Referer":        "https://stockeasy.intellio.kr/",
        })
        self._lock = threading.Lock()  # 세션은 thread-safe하게 사용
        self._load_session()

    def _save_session(self) -> None:
        try:
            cookies = dict(self.session.cookies)
            with open(SESSION_FILE, "w") as f:
                json.dump(cookies, f)
        except Exception as e:
            logger.debug(f"[Session] 저장 실패: {e}")

    def _load_session(self) -> None:
        try:
            if os.path.exists(SESSION_FILE):
                with open(SESSION_FILE) as f:
                    for k, v in json.load(f).items():
                        self.session.cookies.set(k, v)
                logger.info("[Session] 세션 복원")
        except Exception:
            pass

    def login(self) -> bool:
        """로그인 (API → 폼 방식 순서로 시도)."""
        # 1순위: JSON API
        try:
            r = self.session.post(
                LOGIN_URL,
                json={"email": STOCKEASY_EMAIL, "password": STOCKEASY_PASSWORD},
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
            if r.ok and ("token" in r.text or "access" in r.text.lower()):
                logger.info("[Login] API 로그인 성공")
                self._save_session()
                return True
        except Exception as e:
            logger.debug(f"[Login] API 방식 실패: {e}")

        # 2순위: 폼 POST
        try:
            csrf = None
            for url in [
                "https://stockeasy.intellio.kr/login",
                "https://stockeasy.intellio.kr/auth/login",
            ]:
                try:
                    rg   = self.session.get(url, timeout=10)
                    soup = BeautifulSoup(rg.text, "html.parser")
                    inp  = (soup.find("input", {"name": "_token"}) or
                            soup.find("meta",  {"name": "csrf-token"}))
                    if inp:
                        csrf = inp.get("value") or inp.get("content")
                        break
                except Exception:
                    continue

            form = {"email": STOCKEASY_EMAIL, "password": STOCKEASY_PASSWORD}
            if csrf:
                form["_token"] = csrf

            for post_url in [LOGIN_URL, "https://stockeasy.intellio.kr/login"]:
                try:
                    rp = self.session.post(post_url, data=form, timeout=15, allow_redirects=True)
                    if rp.ok and "logout" in rp.text.lower():
                        logger.info(f"[Login] 폼 로그인 성공: {post_url}")
                        self._save_session()
                        return True
                except Exception:
                    continue
        except Exception as e:
            logger.error(f"[Login] 폼 방식 실패: {e}")

        logger.error("[Login] 로그인 실패")
        return False

    def get_strategy_page(self, strategy: str) -> str | None:
        """전략 페이지 HTML. 세션 만료 시 자동 재로그인."""
        url = STRATEGY_URLS.get(strategy)
        if not url:
            return None

        with self._lock:
            for attempt in range(2):
                try:
                    r = self.session.get(url, timeout=15)
                    if "login" in r.url.lower() or "로그인" in r.text[:500]:
                        if attempt == 0:
                            logger.info(f"[Fetch] {strategy} 세션 만료 → 재로그인")
                            self.login()
                            continue
                        logger.error(f"[Fetch] {strategy} 재로그인 후에도 접근 불가")
                        return None
                    return r.text
                except Exception as e:
                    logger.error(f"[Fetch] {strategy} 오류: {e}")
                    return None
        return None


# ──────────────────────────────────────────────────────────────
# HTML 파싱
# ──────────────────────────────────────────────────────────────
def _num(txt: str) -> float:
    t = str(txt).strip().replace(",", "").replace("%", "").replace("원", "")
    try:
        return float(t)
    except Exception:
        return 0.0


def _days(txt: str) -> int:
    t = str(txt).strip()
    NEW_KEYWORDS = ("진입", "진입중", "당일", "신규", "편입", "신규편입",
                    "New", "new", "TODAY", "today", "진입일", "-", "")
    if t in NEW_KEYWORDS:
        return -1
    n = re.sub(r"[^0-9]", "", t)
    try:
        return int(n)
    except Exception:
        return 0


def _parse_entry_date(txt: str) -> str:
    """
    'MM/DD' → 'YYYY-MM-DD' (연도를 현재 연도에서 동적으로 결정).
    - 12월에 1~3월 날짜가 나오면 내년으로 처리 (신규 편입 예정)
    - 파싱된 날짜가 오늘보다 미래(7일 초과)면 작년으로 처리 (과거 편입일)
    """
    t = str(txt).strip()
    if "/" in t:
        parts = t.split("/")
        if len(parts) == 2:
            try:
                mm, dd = int(parts[0]), int(parts[1])
                today  = date.today()
                year   = today.year
                # 12월에 1~3월 날짜 → 내년 편입 예정
                if today.month == 12 and mm <= 3:
                    year += 1
                candidate = date(year, mm, dd)
                # 파싱 결과가 오늘보다 7일 이상 미래 → 작년 날짜
                if (candidate - today).days > 7:
                    year -= 1
                return f"{year}-{mm:02d}-{dd:02d}"
            except Exception:
                pass
    return t


def _parse_sector(txt: str) -> tuple[str, int]:
    """'원자력96' → ('원자력', 96) / '바이오신약84' → ('바이오신약', 84)"""
    t = str(txt).strip()
    m = re.match(r'^(.+?)(\d{1,3})$', t)
    if m:
        return m.group(1).strip(), int(m.group(2))
    return t, 0


def parse_strategy_page(html: str) -> dict:
    """
    스탁이지 전략 페이지 파싱.

    반환:
      holdings: [{name, sector, sector_score, buy_price, current_price,
                  entry_date, hold_days, profit_pct}]
      exits:    [{name, sector, buy_price, sell_price,
                  entry_date, hold_days, profit_pct}]
    """
    soup   = BeautifulSoup(html, "html.parser")
    result = {
        "holdings": [],
        "exits":    [],
        "updated":  datetime.now().strftime("%m/%d %H:%M"),
    }

    # updated 텍스트 파싱
    for el in soup.select("span, p, div"):
        txt = el.get_text(strip=True)
        if "updated" in txt.lower() and "/" in txt:
            result["updated"] = txt
            break

    def _parse_table(table, is_holding: bool) -> list:
        rows_data = []
        for row in table.select("tr"):
            cells = [c.get_text(strip=True) for c in row.select("td")]
            if not cells or len(cells) < 5:
                continue
            if cells[0] in ("섹터", "종목명"):
                continue

            # 7컬럼 (섹터 포함) or 6컬럼 (섹터 없음)
            if len(cells) >= 7:
                sector_raw = cells[0]
                stock_name = cells[1]
                buy_price  = _num(cells[2])
                price2     = _num(cells[3])
                entry_raw  = cells[4]
                hold_raw   = cells[5]
                profit_raw = cells[6]
            elif len(cells) == 6:
                try:
                    float(cells[0].replace(",", ""))
                    continue   # 첫 셀이 숫자면 잘못된 행
                except ValueError:
                    pass
                sector_raw = ""
                stock_name = cells[0]
                buy_price  = _num(cells[1])
                price2     = _num(cells[2])
                entry_raw  = cells[3]
                hold_raw   = cells[4]
                profit_raw = cells[5]
            else:
                continue

            # 종목명이 숫자면 잘못된 파싱 → 스킵
            try:
                float(stock_name.replace(",", ""))
                continue
            except ValueError:
                pass

            if not stock_name or stock_name == "종목명":
                continue

            sector, score = _parse_sector(sector_raw)
            entry_date    = _parse_entry_date(entry_raw)
            hold_days     = _days(hold_raw)
            profit_pct    = _num(profit_raw)

            row_d = {
                "name":         stock_name,
                "sector":       sector,
                "sector_score": score,
                "buy_price":    buy_price,
                "entry_date":   entry_date,
                "hold_days":    hold_days,
                "profit_pct":   profit_pct,
            }
            if is_holding:
                row_d["current_price"] = price2
            else:
                row_d["sell_price"] = price2

            rows_data.append(row_d)
        return rows_data

    for table in soup.select("table"):
        headers    = " ".join(th.get_text(strip=True) for th in table.select("th"))
        if "현재가" in headers:
            result["holdings"] = _parse_table(table, is_holding=True)
        elif "매도가" in headers:
            result["exits"] = _parse_table(table, is_holding=False)

    logger.info(f"[Parse] 보유={len(result['holdings'])} 이탈={len(result['exits'])}")
    return result


# ──────────────────────────────────────────────────────────────
# API 연동
# ──────────────────────────────────────────────────────────────
def api_post(path: str, data: dict) -> dict | None:
    try:
        timeout = API_TIMEOUT_UPDATE if path == "/api/trend/update" else API_TIMEOUT_DEFAULT
        r = httpx.post(f"{BASE_API}{path}", json=data, timeout=timeout)
        return r.json() if r.is_success else None
    except Exception as e:
        logger.error(f"[API POST] {path}: {e}")
        return None


def api_get(path: str) -> dict | list | None:
    try:
        r = httpx.get(f"{BASE_API}{path}", timeout=API_TIMEOUT_DEFAULT)
        return r.json() if r.is_success else None
    except Exception as e:
        logger.error(f"[API GET] {path}: {e}")
        return None


def api_post_background(path: str, data: dict) -> None:
    """비핵심 갱신성 API는 메인 모니터 루프를 막지 않도록 비동기로 전송."""
    if path != "/api/trend/update":
        _API_EXECUTOR.submit(api_post, path, data)
        return

    pending = {f for f in _PENDING_UPDATE_POSTS if not f.done()}
    _PENDING_UPDATE_POSTS.clear()
    _PENDING_UPDATE_POSTS.update(pending)
    if len(_PENDING_UPDATE_POSTS) >= API_UPDATE_MAX_PENDING:
        logger.warning("[API POST] %s skipped: too many pending update requests (%d)", path, len(_PENDING_UPDATE_POSTS))
        return

    fut = _API_EXECUTOR.submit(api_post, path, data)
    _PENDING_UPDATE_POSTS.add(fut)
    fut.add_done_callback(lambda done: _PENDING_UPDATE_POSTS.discard(done))


def calc_quantity(price: float, budget: int = MAX_BUDGET) -> int:
    if not price or price <= 0:
        return 0
    return int(budget // price)


# ──────────────────────────────────────────────────────────────
# 단일 전략 1회 실행
# ──────────────────────────────────────────────────────────────
def run_once(session: StockeasySession, strategy: str) -> None:
    """1개 전략 1회 스크래핑 + 감지 즉시 처리."""
    strategy_name = STRATEGY_NAMES.get(strategy, strategy)
    now_str       = datetime.now().strftime("%Y-%m-%d %H:%M")
    today_str     = date.today().isoformat()
    yesterday_str = (date.today() - timedelta(days=1)).isoformat()

    # ── 페이지 취득 ──────────────────────────────────────────
    html = session.get_strategy_page(strategy)
    if not html:
        logger.warning(f"[{strategy_name}] 페이지 취득 실패")
        return

    data          = parse_strategy_page(html)
    site_holdings = data["holdings"]
    site_exits    = data["exits"]

    # ── DB 포지션 취득 ────────────────────────────────────────
    db_all         = api_get(f"/api/trend/holdings?strategy={strategy}") or []
    db_active      = [h for h in db_all if h.get("is_active")]
    db_active_names = {h["stock_name"] for h in db_active}
    db_all_names    = {h["stock_name"] for h in db_all}  # 활성+비활성 전체
    site_hold_names = {h["name"] for h in site_holdings}
    site_exit_names = {e["name"] for e in site_exits}

    logger.info(
        f"[{strategy_name}] 사이트 보유={len(site_holdings)} 이탈={len(site_exits)}"
        f" | DB 활성={len(db_active)}"
    )

    # ══ Case 1: 신규 편입 ════════════════════════════════════
    for h in site_holdings:
        name       = h["name"]
        hold_days  = h.get("hold_days", 0)
        entry_date = h.get("entry_date", "")

        if name in db_active_names:
            _EXIT_MISS_COUNTS[(strategy, name)] = 0
            # 현재가·수익률 업데이트만
            price = h.get("current_price") or h.get("buy_price") or 0
            api_post_background("/api/trend/update", {
                "stock_name":    name,
                "strategy":      strategy,
                "current_price": price,
                "hold_days":     max(hold_days, 0),
                "profit_pct":    h.get("profit_pct", 0),
                "updated_at":    now_str,
            })
            continue

        # ── 신규 편입 판단 ─────────────────────────────────────
        # 원칙: entry_date가 오늘이거나 hold_days == -1("진입" 텍스트)
        is_new   = False
        new_why  = ""

        if hold_days == -1:
            # "진입" 텍스트 — 가장 확실한 신규 신호
            is_new  = True
            new_why = "진입텍스트"
        elif entry_date == today_str:
            # 오늘 편입일 (hold_days=0 or 1 or 2까지 허용)
            if hold_days <= 2:
                is_new  = True
                new_why = f"오늘편입+{hold_days}일"
        elif entry_date == yesterday_str and hold_days in (0, 1):
            # 장 마감 후 편입 → 사이트가 다음날 아침 업데이트하는 경우
            is_new  = True
            new_why = f"어제편입+{hold_days}일(장마감후편입)"

        if not is_new:
            # 복구 편입: DB에 한 번도 없었던 종목은 hold_days 무관하게 동기화
            # (봇 다운·세션 만료 등으로 편입 감지 윈도우를 놓친 경우)
            if name not in db_all_names:
                is_new  = True
                new_why = f"복구편입(DB누락+{hold_days}일보유)"
                logger.warning(
                    f"[복구편입] {strategy_name} / {name} — DB에 이력 없음 "
                    f"hold={hold_days}일 entry={entry_date} → 강제 편입"
                )
            else:
                logger.debug(
                    f"[스킵] {name} hold={hold_days}일 entry={entry_date} "
                    f"(오늘={today_str}) — 신규 아님"
                )
                continue

        logger.info(f"[신규감지] ★ {strategy_name} / {name} — {new_why}")

        # 즉시 매수 처리 + 텔레그램 전송
        price = h.get("current_price") or h.get("buy_price") or 0
        qty   = calc_quantity(price)
        total = round(price * qty)

        result = api_post("/api/trend/buy", {
            "stock_name":   name,
            "sector":       h.get("sector", ""),
            "sector_score": h.get("sector_score", 0),
            "buy_price":    price,
            "quantity":     qty,
            "entry_date":   entry_date,
            "hold_days":    0,
            "detected_at":  now_str,
            "strategy":     strategy,
        })

        if result and result.get("status") == "ok":
            sector_str = f" [{h['sector']}({h['sector_score']})]" if h.get("sector") else ""
            msg = (
                f"🟢 <b>[{strategy_name}] 신규 편입 감지!</b>{sector_str}\n"
                f"종목: <b>{name}</b>\n"
                f"현재가: <b>{price:,.0f}원</b>\n"
                f"가상매수: {qty:,}주 × {price:,.0f}원 = <b>{total:,.0f}원</b>\n"
                f"편입일: {entry_date} | 감지: {now_str}"
            )
            dedup_key = f"buy_{strategy}_{name}_{entry_date}"
            _cur_h = datetime.now().hour
            if not (8 <= _cur_h < 22):  # 22:00~08:00 야간 알림 억제
                logger.info(f"[야간억제] {name} 신규편입 감지 (야간 알림 보류)")
                sent = False
            else:
                sent = send_telegram(msg, key=dedup_key)
            if sent:
                logger.info(f"[알림] ✅ [{strategy_name}] {name} 신규편입 텔레그램 발송")
        else:
            logger.warning(f"[신규편입] {name}: DB 저장 실패 result={result}")

    # ══ Case 2: 이탈 감지 ════════════════════════════════════
    for db_h in db_active:
        name = db_h["stock_name"]

        if db_h.get("strategy", "peak") != strategy:
            continue
        if name in site_hold_names:
            _EXIT_MISS_COUNTS[(strategy, name)] = 0
            continue  # 아직 보유중

        # 사이트 이탈 테이블에 명시되었으면 즉시 확정, 아니면 연속 누락 N회 확인 후 확정
        miss_key = (strategy, name)
        _EXIT_MISS_COUNTS[miss_key] = int(_EXIT_MISS_COUNTS.get(miss_key, 0)) + 1
        miss_cnt = _EXIT_MISS_COUNTS[miss_key]
        if name not in site_exit_names and miss_cnt < EXIT_MISS_CONFIRM_COUNT:
            logger.warning(
                f"[편출보류] {strategy_name} / {name} "
                f"(site_exits 미포함, 연속누락 {miss_cnt}/{EXIT_MISS_CONFIRM_COUNT})"
            )
            continue

        # 이탈 처리
        site_exit = next((e for e in site_exits if e["name"] == name), None)
        sell_price = (
            site_exit.get("sell_price", 0) if site_exit
            else db_h.get("current_price") or db_h.get("buy_price") or 0
        )
        buy_price = db_h.get("buy_price", 0) or 0
        qty       = db_h.get("quantity",  0) or 0
        profit    = round((sell_price - buy_price) * qty)
        pct       = round((sell_price - buy_price) / buy_price * 100, 2) if buy_price else 0
        hold_days = (
            site_exit.get("hold_days", 0) if site_exit
            else db_h.get("hold_days", 0)
        )

        logger.info(f"[이탈] {strategy_name} / {name} 매도가={sell_price:,.0f} 손익={profit:+,.0f}원")

        result = api_post("/api/trend/sell", {
            "stock_name": name,
            "strategy":   strategy,
            "sell_price": sell_price,
            "sold_at":    now_str,
            "profit_pct": pct,
        })

        if result and result.get("status") == "ok":
            _EXIT_MISS_COUNTS[miss_key] = 0
            emoji = "🔴" if profit < 0 else ("🟡" if profit == 0 else "🟢")
            msg = (
                f"{emoji} <b>[{strategy_name}] 이탈 종목</b>\n"
                f"종목: <b>{name}</b>\n"
                f"매수가: {buy_price:,.0f}원 → 매도가: <b>{sell_price:,.0f}원</b>\n"
                f"보유: {hold_days}일 | 수량: {qty:,}주\n"
                f"손익: <b>{profit:+,.0f}원 ({pct:+.2f}%)</b>\n"
                f"확인: {now_str}"
            )
            dedup_key = f"sell_{strategy}_{name}_{today_str}"
            _cur_h = datetime.now().hour
            if not (8 <= _cur_h < 22):  # 22:00~08:00 야간 알림 억제
                logger.info(f"[야간억제] {name} 이탈 감지 (야간 알림 보류)")
                sent = False
            else:
                sent = send_telegram(msg, key=dedup_key)
            if sent:
                logger.info(f"[알림] ✅ [{strategy_name}] {name} 이탈 텔레그램 발송")
        else:
            logger.warning(f"[이탈] {name}: DB 처리 실패 result={result}")


# ──────────────────────────────────────────────────────────────
# 3전략 병렬 실행
# ──────────────────────────────────────────────────────────────
def run_all_strategies(session: StockeasySession) -> None:
    """3개 전략을 ThreadPoolExecutor로 병렬 실행."""
    strategies = list(STRATEGY_NAMES.keys())

    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="monitor") as ex:
        futures = {
            ex.submit(run_once, session, strat): strat
            for strat in strategies
        }
        for fut in as_completed(futures):
            strat = futures[fut]
            try:
                fut.result()
            except Exception as e:
                logger.error(f"[병렬실행] {STRATEGY_NAMES[strat]} 오류: {e}")


# ──────────────────────────────────────────────────────────────
# 진입점
# ──────────────────────────────────────────────────────────────
def main() -> None:
    once = "--once" in sys.argv

    logger.info("=" * 60)
    logger.info("Peak Monitor 시작 (즉시 알림 버전)")
    logger.info(f"  장마감(15:00~16:30): {INTERVAL_MARKET_CLOSE}초  "
                f"장중: {INTERVAL_MARKET_OPEN}초  "
                f"야간: {INTERVAL_EVENING}초  "
                f"심야: {INTERVAL_NIGHT}초  "
                f"주말: {INTERVAL_WEEKEND}초")
    logger.info(f"  텔레그램: {'✅ 설정됨' if config.TELEGRAM_BOT_TOKEN else '❌ 미설정'}")
    logger.info("=" * 60)

    _load_sent_history()
    from notifier import _sent as _notifier_sent
    logger.info(f"[Init] 전송 이력: {len(_notifier_sent)}건 로드")

    session = StockeasySession()
    if not session.login():
        logger.warning("[Init] 초기 로그인 실패 — 계속 진행")

    if once:
        logger.info("[테스트] 1회 실행 모드")
        run_all_strategies(session)
        return

    # 자정 이력 정리 스레드
    def _daily_cleanup():
        while True:
            now = datetime.now()
            nxt = (now + timedelta(days=1)).replace(hour=0, minute=1, second=0, microsecond=0)
            time.sleep(max(0, (nxt - datetime.now()).total_seconds()))
            _clear_old_sent_keys()

    threading.Thread(target=_daily_cleanup, daemon=True, name="SentKeyCleanup").start()

    # 메인 루프
    loop_count = 0
    while True:
        interval = get_poll_interval()
        t_start  = time.monotonic()

        try:
            run_all_strategies(session)
        except Exception as e:
            logger.error(f"[루프] 오류: {e}")

        loop_count += 1
        elapsed  = time.monotonic() - t_start
        sleep_t  = max(0, interval - elapsed)

        now_str  = datetime.now().strftime("%H:%M:%S")
        next_str = (datetime.now() + timedelta(seconds=sleep_t)).strftime("%H:%M:%S")
        logger.info(
            f"[루프 #{loop_count}] 완료 ({elapsed:.1f}초) — "
            f"다음 실행: {next_str} (폴링:{interval//60}분{interval%60}초)"
        )
        time.sleep(sleep_t)


def _acquire_daemon_lock() -> None:
    """중복 실행 방지. 이미 실행 중이면 즉시 종료."""
    import atexit
    pid_path = "/Applications/stock_dashboard/logs/peak_monitor.pid"
    os.makedirs(os.path.dirname(pid_path), exist_ok=True)

    if os.path.exists(pid_path):
        try:
            existing = int(open(pid_path).read().strip())
            if existing == os.getpid():
                # serve_foreground.sh가 먼저 PID를 썼을 때 자기 자신을 중복으로 오인하는 방지
                pass
            else:
                os.kill(existing, 0)
                logger.warning(f"[중복 실행 방지] PID {existing}가 이미 실행 중입니다. 종료합니다.")
                raise SystemExit(0)
        except (ProcessLookupError, ValueError):
            pass

    with open(pid_path, "w") as _f:
        _f.write(str(os.getpid()))

    atexit.register(lambda: os.unlink(pid_path) if os.path.exists(pid_path) else None)
    logger.info(f"[시작] PID {os.getpid()} — {pid_path} 잠금 획득")


if __name__ == "__main__":
    _acquire_daemon_lock()
    main()
