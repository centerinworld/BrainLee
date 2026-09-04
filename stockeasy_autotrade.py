from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta

from db_utils import connect_stock_db
from notifier import send as send_telegram
from kis_client import kis_client
from live_trading_data import evaluate_live_data_contract, record_execution_snapshot
from stockeasy_analyzer import fetch_strategy_api

logger = logging.getLogger(__name__)

LIVE_START_DATE = os.getenv("STOCKEASY_LIVE_START_DATE", "2026-05-18")
PER_STOCK_BUDGET = int(os.getenv("STOCKEASY_PER_STOCK_BUDGET_KRW", "2000000"))
# peak_monitor.py의 calc_quantity()와 동일한 가상매매 예산(1,000만원/종목).
# peak/momentum/value 3개 전략이 peak_holding 테이블을 공유하므로 수량 산정 기준을
# 통일해야 한다(2026-09-01 발견: 이 상수 없이 quantity=0으로 하드코딩되어 있던
# 버그로 인해 활성 보유종목의 수익금이 항상 0원으로 표시되던 문제 수정).
TREND_HOLDING_TICKET_KRW = 10_000_000
ENABLE_LIVE = (os.getenv("STOCKEASY_LIVE_AUTOTRADE", "false").lower() == "true")
LIVE_APPROVED_STRATEGIES = {
    value.strip()
    for value in os.getenv("STOCKEASY_LIVE_APPROVED_STRATEGIES", "").split(",")
    if value.strip()
}

# ── Live order guard rails (fail-close) ─────────────────────────
LIVE_MAX_KIWOOM_KIS_GAP_PCT = float(os.getenv("LIVE_MAX_KIWOOM_KIS_GAP_PCT", "1.5"))
LIVE_KIWOOM_STALE_SEC = int(os.getenv("LIVE_KIWOOM_STALE_SEC", "180"))
LIVE_CONSEC_FAIL_LIMIT = int(os.getenv("LIVE_CONSEC_FAIL_LIMIT", "3"))
LIVE_FAIL_PAUSE_MIN = int(os.getenv("LIVE_FAIL_PAUSE_MIN", "30"))
LIVE_BUY_CASH_BUFFER_PCT = float(os.getenv("STOCKEASY_LIVE_BUY_CASH_BUFFER_PCT", "1.02"))

STRATEGIES = {
    # se_momentum is retired by strategy governance (worst period -43.32%).
    "momentum": {"label": "모멘텀 Easy", "portfolio_id": 1, "live": False},
    "peak": {"label": "Peak Easy", "portfolio_id": 2, "live": False},
    "value": {"label": "벨류 Easy", "portfolio_id": 3, "live": False},
}


def _conn():
    c = connect_stock_db(timeout=30)
    c.row_factory = sqlite3.Row
    return c


def _ensure_tables(c: sqlite3.Connection) -> None:
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS stockeasy_sync_state (
            strategy TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            first_seen_at TEXT,
            last_seen_at TEXT,
            updated_at TEXT,
            PRIMARY KEY(strategy, stock_code)
        );

        CREATE TABLE IF NOT EXISTS autotrade_guard_state (
            k TEXT PRIMARY KEY,
            v TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS stockeasy_autotrade_manual_hold (
            strategy TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (strategy, stock_code)
        );
        """
    )
    c.commit()


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _is_live_window() -> bool:
    return ENABLE_LIVE and _today_str() >= LIVE_START_DATE


def _is_kr_market_open() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    hm = now.hour * 100 + now.minute
    return 900 <= hm < 1530


def _is_nxt_time() -> bool:
    """NXT 시간대(간주): 장후/장전 확장 구간."""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    hm = now.hour * 100 + now.minute
    return (800 <= hm < 900) or (1530 <= hm < 2000)


def _first_present(d: dict, *keys, default=0):
    """`a or b or default` 패턴은 a=0(정상값)일 때도 falsy로 취급해 b로 조용히
    대체해버리는 버그가 있음(2026-07-21 발견 — hold_days=0/priority_score=0처럼
    "값이 있는데 0"인 경우와 "키 자체가 없음"을 구분 못함). 이 헬퍼는 None인 필드만
    건너뛰고, 0/False처럼 정상적으로 존재하는 falsy 값은 그대로 사용한다."""
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return default


def _is_fresh_entry(entry_date: str, hold_days: int) -> bool:
    """
    '실제 신규 편입' 판정:
    - hold_days == -1 (진입 텍스트)
    - hold_days <= 2
    - entry_date가 오늘/어제
    위 조건 외(오래된 보유)는 신규 추가하지 않음.
    """
    today = _today_str()
    yday = (datetime.now().date() - timedelta(days=1)).strftime("%Y-%m-%d")
    if hold_days == -1:
        return True
    if hold_days <= 2:
        return True
    if entry_date in (today, yday):
        return True
    return False


def _reconfirm_buy_signal(strategy: str, stock_code: str) -> tuple[bool, str]:
    """실주문 직전 2차 검증: 최신 전략 데이터에서 해당 종목이 여전히 신규 편입 신호인지 확인."""
    try:
        # 2026-07-21: fetch 실패(None)와 "성공+데이터 없음"을 구분 — 실패 시엔 이유를
        # 명확히 남기고(매수 보류는 동일하게 안전하지만 원인 진단이 가능해야 함)
        latest = fetch_strategy_api(strategy)
        if latest is None:
            return False, "reconfirm_fetch_failed"
        holdings = latest.get("holdings") or []
        target = None
        for h in holdings:
            code = str(h.get("stock_code") or "").zfill(6)
            if code == stock_code:
                target = h
                break
        if not target:
            return False, "reconfirm_not_in_latest_holdings"

        hold_days = int(_first_present(target, "hold_days", "holding_days", default=0))
        entry_date = str(target.get("entry_date") or _today_str())
        if not _is_fresh_entry(entry_date, hold_days):
            return False, f"reconfirm_not_fresh(hold_days={hold_days},entry_date={entry_date})"
        return True, "ok"
    except Exception as e:
        return False, f"reconfirm_error:{e}"


def _upsert_trend_holding(strategy: str, stock_code: str, stock_name: str, price: float, entry_date: str):
    c = _conn()
    row = c.execute(
        "SELECT id FROM peak_holding WHERE stock_code=? AND strategy=? AND is_active=1 ORDER BY id DESC LIMIT 1",
        (stock_code, strategy),
    ).fetchone()
    if row:
        c.execute("UPDATE peak_holding SET current_price=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (price, row["id"]))
        c.commit()
        c.close()
        return
    qty = int(TREND_HOLDING_TICKET_KRW // price) if price and price > 0 else 0
    amount = round(price * qty)
    c.execute(
        """
        INSERT INTO peak_holding(
            stock_code, stock_name, sector, buy_price, current_price, quantity,
            entry_date, hold_days, profit_pct, is_active, strategy, detected_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,0,0.0,1,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
        """,
        (stock_code, stock_name, "", price, price, qty, entry_date, strategy),
    )
    c.execute(
        "INSERT INTO peak_trade(stock_name, tx_type, price, quantity, total_amount, profit, profit_pct, tx_at, strategy) VALUES (?,?,?,?,?,?,?,CURRENT_TIMESTAMP,?)",
        (stock_name, "buy", price, qty, amount, 0, 0.0, strategy),
    )
    c.commit()
    c.close()


def _deactivate_trend_holding(strategy: str, stock_name: str, sell_price: float):
    c = _conn()
    row = c.execute(
        "SELECT id, buy_price, quantity FROM peak_holding WHERE stock_name=? AND strategy=? AND is_active=1 ORDER BY id DESC LIMIT 1",
        (stock_name, strategy),
    ).fetchone()
    if not row:
        c.close()
        return
    buy_price = float(row["buy_price"] or 0)
    qty = int(row["quantity"] or 0)
    profit = (sell_price - buy_price) * qty if buy_price > 0 else 0.0
    profit_pct = ((sell_price - buy_price) / buy_price * 100.0) if buy_price > 0 else 0.0
    c.execute(
        "UPDATE peak_holding SET is_active=0, sell_price=?, sold_at=?, current_price=?, profit_pct=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (sell_price, _now_str(), sell_price, profit_pct, row["id"]),
    )
    c.execute(
        "INSERT INTO peak_trade(stock_name, tx_type, price, quantity, total_amount, profit, profit_pct, tx_at, strategy) VALUES (?,?,?,?,?,?,?,CURRENT_TIMESTAMP,?)",
        (stock_name, "sell", sell_price, qty, round(sell_price * qty), profit, profit_pct, strategy),
    )
    c.commit()
    c.close()


def _place_live_order(stock_code: str, side: str, qty: int, strategy_key: str) -> tuple[bool, str]:
    """Fail closed until a reconciled broker lifecycle is implemented.

    StockEasy remains an analysis/signal source.  It must never bypass the
    shared order lifecycle with a direct broker request, even if an environment
    variable or strategy flag is changed accidentally.  Paper execution is
    handled by the Strategy Center path; a future live adapter must create an
    idempotent order, reconcile broker acknowledgements/fills, and then replace
    this explicit block.
    """
    _record_guard_result(False)
    return False, "live_execution_disabled: unified broker lifecycle is not implemented"


def _is_manual_hold(strategy: str, code: str) -> bool:
    """수동 보류(자동매도 제외) 종목인지 확인 — 2026-07-21 도입.
    사용자가 특정 종목을 자동매도 로직에서 명시적으로 제외하고 싶을 때 사용
    (예: 버그로 인해 실제 청산 타이밍이 애매해진 기존 보유분을 직접 판단하고 싶은 경우).
    독립 커넥션 사용 — 호출 시점(매수/매도 실행 루프)엔 메인 sync 커넥션이 이미 닫혀있음."""
    c = _conn()
    try:
        _ensure_tables(c)
        r = c.execute(
            "SELECT 1 FROM stockeasy_autotrade_manual_hold WHERE strategy=? AND stock_code=?",
            (strategy, code),
        ).fetchone()
        return r is not None
    finally:
        c.close()


def _guard_get(c: sqlite3.Connection, key: str, default: str = "") -> str:
    r = c.execute("SELECT v FROM autotrade_guard_state WHERE k=?", (key,)).fetchone()
    return str(r[0]) if r and r[0] is not None else default


def _guard_set(c: sqlite3.Connection, key: str, value: str) -> None:
    c.execute(
        """
        INSERT INTO autotrade_guard_state(k, v, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(k) DO UPDATE SET v=excluded.v, updated_at=excluded.updated_at
        """,
        (key, value, _now_str()),
    )


def _is_paused_by_guard() -> tuple[bool, str]:
    c = _conn()
    try:
        _ensure_tables(c)
        until_epoch = float(_guard_get(c, "pause_until_epoch", "0") or "0")
        now_epoch = time.time()
        if until_epoch > now_epoch:
            left_min = int((until_epoch - now_epoch) // 60) + 1
            return True, f"guard_pause_active({left_min}m_left)"
        return False, ""
    finally:
        c.close()


def _record_guard_result(ok: bool) -> None:
    c = _conn()
    try:
        _ensure_tables(c)
        fail_cnt = int(float(_guard_get(c, "consecutive_fail", "0") or "0"))
        if ok:
            _guard_set(c, "consecutive_fail", "0")
            c.commit()
            return

        fail_cnt += 1
        _guard_set(c, "consecutive_fail", str(fail_cnt))
        if fail_cnt >= LIVE_CONSEC_FAIL_LIMIT:
            pause_until = time.time() + LIVE_FAIL_PAUSE_MIN * 60
            _guard_set(c, "pause_until_epoch", str(pause_until))
            send_telegram(
                f"⛔ [실주문가드] 연속 실패 {fail_cnt}회로 자동 주문 일시중지\n"
                f"중지시간: {LIVE_FAIL_PAUSE_MIN}분\n"
                f"해제 후 재시도 전에 데이터/계좌 상태 점검 필요",
                key=f"guard_pause_{_today_str()}_{int(pause_until)}",
            )
        c.commit()
    finally:
        c.close()


def _latest_kiwoom_snapshot(stock_code: str) -> tuple[float, float]:
    """returns (price, age_sec). price=0 if unavailable."""
    c = _conn()
    try:
        r = c.execute(
            """
            SELECT last_price, updated_at
            FROM kiwoom_realtime_quote
            WHERE stock_code=?
            ORDER BY datetime(updated_at) DESC
            LIMIT 1
            """,
            (stock_code,),
        ).fetchone()
    finally:
        c.close()
    if not r or not r[0] or not r[1]:
        return 0.0, 999999.0
    try:
        ts = datetime.strptime(str(r[1])[:19], "%Y-%m-%d %H:%M:%S")
        age = max(0.0, (datetime.now() - ts).total_seconds())
    except Exception:
        age = 999999.0
    return float(r[0]), float(age)


def _preflight_order_guard(stock_code: str, side: str, qty: int) -> tuple[bool, str]:
    if qty <= 0:
        return False, "guard_qty_invalid"

    paused, reason = _is_paused_by_guard()
    if paused:
        return False, reason

    # KIS 기준가격은 필수. 없으면 주문 금지.
    kis_px = 0.0
    try:
        p = kis_client.get_current_price(stock_code) or {}
        kis_px = float(p.get("close") or 0)
    except Exception:
        kis_px = 0.0
    if kis_px <= 0:
        return False, "guard_kis_price_missing"

    # 키움 실시간이 최근값이면 교차검증. 괴리 과도 시 주문 차단.
    kw_px, kw_age = _latest_kiwoom_snapshot(stock_code)
    if kw_px > 0 and kw_age <= LIVE_KIWOOM_STALE_SEC:
        gap = abs(kw_px - kis_px) / kis_px * 100.0
        if gap > LIVE_MAX_KIWOOM_KIS_GAP_PCT:
            return False, f"guard_price_gap({gap:.2f}%)"

    if side == "buy":
        snapshot = kis_client.get_account_snapshot() or {"summary": {}}
        summary = snapshot.get("summary") or {}
        strict_cash = float(summary.get("strict_cash_available") or 0.0)
        required = float(qty) * kis_px * LIVE_BUY_CASH_BUFFER_PCT
        if strict_cash <= 0:
            return False, "guard_strict_cash_missing"
        if strict_cash < required:
            return False, f"guard_cash_short(strict={strict_cash:,.0f}<required={required:,.0f})"

    return True, "ok"


def _sync_one_strategy(strategy: str) -> dict:
    now = _now_str()
    meta = STRATEGIES[strategy]
    label = meta["label"]
    live_for_strategy = bool(meta["live"])

    # 2026-07-21 버그 수정: `fetch_strategy_api() or {}`가 "API 호출 실패"(None)와
    # "API 성공+실제 보유0건(전량 매도)"을 동일하게 취급해, 전략이 실제로 전량 청산됐을 때도
    # early-return으로 removed(매도) 계산을 건너뛰어 DB/실주문 모두 갱신되지 않던 문제.
    # momentum은 live=True(실주문) 전략이라 이 버그로 실계좌 매도가 누락될 수 있었음.
    site = fetch_strategy_api(strategy)
    if site is None:
        logger.warning("[StockEasyAutoTrade] %s API 조회 실패(None)", strategy)
        return {"strategy": strategy, "ok": False, "reason": "fetch_failed"}
    holdings = site.get("holdings") or []
    if not holdings:
        logger.info("[StockEasyAutoTrade] %s 보유종목 0건(전량 청산 상태) — 정상 처리 계속", strategy)

    current = {}
    for h in holdings:
        code = str(h.get("stock_code") or "").zfill(6)
        if not code or code == "000000":
            continue
        # 2026-07-21: trend_score/score/sector_score가 0(정상적으로 낮은 점수)이어도
        # `or` 체인이 falsy로 취급해 조용히 다른 지표로 대체해버리던 버그 수정.
        # 우선순위(trend_score > score > sector_score)는 그대로 유지하되, "값이 있는데
        # 0"과 "키가 아예 없음"을 구분한다.
        try:
            priority_score = float(_first_present(h, "trend_score", "score", "sector_score", default=0))
        except Exception:
            priority_score = 0.0
        current[code] = {
            "stock_code": code,
            "stock_name": (h.get("name") or code).strip(),
            "price": float(h.get("current_price") or h.get("buy_price") or 0),
            "ref_buy_price": float(h.get("buy_price") or 0),
            "entry_date": h.get("entry_date") or _today_str(),
            "hold_days": int(_first_present(h, "hold_days", "holding_days", default=0)),
            "priority_score": priority_score,
        }

    c = _conn()
    _ensure_tables(c)
    db_rows = c.execute(
        "SELECT stock_code, stock_name, is_active FROM stockeasy_sync_state WHERE strategy=?",
        (strategy,),
    ).fetchall()
    first_sync = (len(db_rows) == 0)
    prev_active = {r["stock_code"]: r for r in db_rows if int(r["is_active"] or 0) == 1}
    current_codes = set(current.keys())
    prev_codes = set(prev_active.keys())

    # 2026-08-22: fetch_strategy_api()가 스탁이지 사이트 네트워크 지연/타임아웃 등으로
    # 일부 섹터만 불완전하게 반환할 때, current_codes가 급감해 정상 보유종목 다수가
    # "편출"로 오판되고 텔레그램 알림이 대량 발송되는 문제 실측 확인(8/19 momentum
    # 20종목 동시 편출 오탐 → 8/20 그중 2종목 재편입 확인, 전형적 데이터 플랩).
    # prev가 10개 이상인데 current가 그 절반 미만이면 이번 사이클은 API 이상 응답으로
    # 판단해 DB 갱신/알림 없이 건너뛴다(다음 정상 사이클에서 다시 비교).
    if not first_sync and len(prev_codes) >= 10 and len(current_codes) < len(prev_codes) * 0.5:
        c.close()
        logger.warning(
            "[StockEasyAutoTrade] %s API 응답 급감 의심(prev=%d current=%d) — 이번 동기화 스킵",
            strategy, len(prev_codes), len(current_codes)
        )
        return {
            "strategy": strategy, "ok": False, "reason": "suspected_partial_response",
            "prev_count": len(prev_codes), "current_count": len(current_codes),
            "added": [], "removed": [], "buy_done": [], "sell_done": [], "buy_fail": [], "sell_fail": [],
        }

    added = sorted(list(current_codes - prev_codes))
    removed = sorted(list(prev_codes - current_codes))

    for code, d in current.items():
        c.execute(
            """
            INSERT INTO stockeasy_sync_state(strategy, stock_code, stock_name, is_active, first_seen_at, last_seen_at, updated_at)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(strategy, stock_code) DO UPDATE SET
              stock_name=excluded.stock_name,
              is_active=1,
              last_seen_at=excluded.last_seen_at,
              updated_at=excluded.updated_at
            """,
            (strategy, code, d["stock_name"], 1, now, now, now),
        )
    for code in removed:
        c.execute(
            "UPDATE stockeasy_sync_state SET is_active=0, updated_at=? WHERE strategy=? AND stock_code=?",
            (now, strategy, code),
        )
    c.commit()
    c.close()

    if first_sync:
        logger.info("[StockEasyAutoTrade] %s first-sync bootstrap (alerts/orders skipped)", strategy)
        return {
            "strategy": strategy,
            "ok": True,
            "bootstrap": True,
            "added": [],
            "removed": [],
            "buy_done": [],
            "sell_done": [],
            "buy_fail": [],
            "sell_fail": [],
            "live_enabled": False,
            "market_open": _is_kr_market_open(),
        }

    snapshot = kis_client.get_account_snapshot() or {"holdings": [], "summary": {}}
    summary = snapshot.get("summary") or {}
    cash = float(summary.get("strict_cash_available") or 0.0)
    acct_holdings = {
        str(h.get("stock_code") or "").zfill(6): int(float(h.get("quantity") or 0))
        for h in (snapshot.get("holdings") or [])
    }

    # A global switch alone must never promote a strategy into live trading.
    live_enabled = _is_live_window() and live_for_strategy and strategy in LIVE_APPROVED_STRATEGIES
    market_open = _is_kr_market_open()
    nxt_time = _is_nxt_time()
    buy_done, sell_done, buy_fail, sell_fail = [], [], [], []

    # 예수금 부족 시 우선순위 매수: 추세추종 점수(없으면 sector_score) 높은 순
    added_sorted = sorted(added, key=lambda cd: float((current.get(cd) or {}).get("priority_score") or 0), reverse=True)

    for code in added_sorted:
        d = current[code]
        # 오래 보유로 보이는 종목은 '신규 편입'으로 추가하지 않음
        # (기존 보유 데이터는 건드리지 않음)
        if not _is_fresh_entry(d.get("entry_date", ""), int(d.get("hold_days", 0))):
            logger.info(
                "[StockEasyAutoTrade] %s skip non-fresh %s(%s) hold_days=%s entry_date=%s",
                strategy, d["stock_name"], code, d.get("hold_days", 0), d.get("entry_date", "")
            )
            continue
        _upsert_trend_holding(strategy, code, d["stock_name"], d["price"], d["entry_date"])
        send_telegram(
            f"🟢 [StockEasy/{label}] 신규 편입\n{d['stock_name']}({code})\n감지: {now}",
            key=f"se_sync_add_{strategy}_{code}_{_today_str()}",
        )
        if not live_enabled:
            continue
        if acct_holdings.get(code, 0) > 0:
            continue
        px = float(d["price"] or 0)
        if px <= 0:
            p = kis_client.get_current_price(code) or {}
            px = float(p.get("close") or 0)
        if px <= 0:
            buy_fail.append((code, "가격조회 실패"))
            continue
        # NXT 시간대에는 '스탁이지 매수가보다 싼 경우' 즉시 매수
        ref_buy = float(d.get("ref_buy_price") or 0)
        if (not market_open) and nxt_time and ref_buy > 0 and px > ref_buy:
            buy_fail.append((code, f"NXT 대기(현재가 {px:,.0f} > 스탁이지매수가 {ref_buy:,.0f})"))
            continue
        budget = min(PER_STOCK_BUDGET, int(cash))
        qty = int(budget // px)
        if qty <= 0:
            buy_fail.append((code, "예수금 부족"))
            continue

        # 실주문 직전 최신 신호 2차 검증 (fail-close)
        reconfirm_ok, reconfirm_reason = _reconfirm_buy_signal(strategy=strategy, stock_code=code)
        if not reconfirm_ok:
            buy_fail.append((code, reconfirm_reason))
            logger.warning("[StockEasyAutoTrade] %s buy blocked by reconfirm: %s %s", strategy, code, reconfirm_reason)
            continue

        ok, reason = _place_live_order(code, "buy", qty, strategy)
        if ok:
            amount = int(qty * px)
            cash -= amount
            buy_done.append((code, qty, amount))
            send_telegram(
                f"✅ [실계좌 매수/{label}] {d['stock_name']}({code})\n수량: {qty}주\n주문금액: 약 {amount:,.0f}원\n사유: StockEasy 신규 편입",
                key=f"se_live_buy_{strategy}_{code}_{_today_str()}",
            )
        else:
            buy_fail.append((code, reason))

    for code in removed:
        row = prev_active.get(code)
        stock_name = (row["stock_name"] if row else code) or code
        px_info = kis_client.get_current_price(code) or {}
        sell_px = float(px_info.get("close") or 0)
        _deactivate_trend_holding(strategy, stock_name, sell_px)
        send_telegram(
            f"🔴 [StockEasy/{label}] 편출\n{stock_name}({code})\n감지: {now}",
            key=f"se_sync_remove_{strategy}_{code}_{_today_str()}",
        )
        if not live_enabled:
            continue
        qty = int(acct_holdings.get(code, 0))
        if qty <= 0:
            continue
        if _is_manual_hold(strategy, code):
            # 2026-07-21: 사용자가 명시적으로 자동매도 제외 지정한 종목 — 편출 감지/알림은
            # 정상 수행하되 실주문은 생략(사용자가 직접 판단해 처리).
            send_telegram(
                f"⏸️ [StockEasy/{label}] 편출 감지했지만 수동보류 설정으로 자동매도 생략\n"
                f"{stock_name}({code}) — 직접 확인 후 처리해주세요.",
                key=f"se_manual_hold_skip_{strategy}_{code}_{_today_str()}",
            )
            continue
        ok, reason = _place_live_order(code, "sell", qty, strategy)
        if ok:
            amount = int(qty * sell_px) if sell_px > 0 else 0
            sell_done.append((code, qty, amount))
            send_telegram(
                f"✅ [실계좌 매도/{label}] {stock_name}({code})\n수량: {qty}주\n주문금액: 약 {amount:,.0f}원\n사유: StockEasy 편출",
                key=f"se_live_sell_{strategy}_{code}_{_today_str()}",
            )
        else:
            sell_fail.append((code, reason))

    logger.info(
        "[StockEasyAutoTrade] %s added=%d removed=%d buy_ok=%d sell_ok=%d buy_fail=%d sell_fail=%d live=%s market=%s",
        strategy, len(added), len(removed), len(buy_done), len(sell_done), len(buy_fail), len(sell_fail), live_enabled, market_open
    )
    return {
        "strategy": strategy,
        "ok": True,
        "added": added,
        "removed": removed,
        "buy_done": buy_done,
        "sell_done": sell_done,
        "buy_fail": buy_fail,
        "sell_fail": sell_fail,
        "live_enabled": live_enabled,
        "market_open": market_open,
        "nxt_time": nxt_time,
    }


def run_stockeasy_all_strategies_sync() -> dict:
    out = {"ok": True, "results": {}}
    for s in ("momentum", "peak", "value"):
        out["results"][s] = _sync_one_strategy(s)
    return out
