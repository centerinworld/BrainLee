"""paper_adapters.py — 전략센터 BUY 신호 → D+1 시가 가상체결 어댑터 (2026-10-02)

워크플로:
  16:30 intake_signals() — live_signal_registry BUY_CANDIDATE → paper_order_queue(pending)
  09:10 fill_pending()   — pending 주문을 D+1 시가로 체결 (paper_execution.settle 호출)

전략 이름: "sc_paper"
포지션 규모: 1천만원 / 종목, 최대 10개 동시 보유
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime

import db_compat
import paper_execution as pe
from virtual_trading_ledger import available_cash

logger = logging.getLogger(__name__)

STRATEGY = "sc_paper"
POSITION_SIZE_KRW = 10_000_000      # 종목당 1천만원
MAX_POSITIONS = 10                  # 최대 보유 수
STOP_LOSS_PCT = -0.10               # -10% 손절
TAKE_PROFIT_PCT = 0.20              # +20% 익절
MAX_HOLD_DAYS = 30                  # 최대 보유일


def _conn():
    return db_compat.connect_primary_db()


def _mkt_cap_of(conn, code: str, day: str, price: float) -> float:
    """price_history의 shares_outstanding으로 시총 추산. 없으면 기본값 500억."""
    row = conn.execute(
        "SELECT shares_outstanding FROM price_history "
        "WHERE stock_code=%s AND substr(date,1,10)<=%s AND shares_outstanding>0 "
        "ORDER BY date DESC LIMIT 1",
        (code, day),
    ).fetchone()
    if row and row[0]:
        return float(row[0]) * price / 1e8  # 억 단위
    return 500.0


def intake_signals(conn=None, as_of_date: str | None = None) -> int:
    """당일 BUY_CANDIDATE 신호를 paper_order_queue에 등록. 반환: 신규 등록 건수."""
    close_conn = conn is None
    if conn is None:
        conn = _conn()
    today = as_of_date or date.today().isoformat()
    pe.ensure(conn)

    rows = conn.execute(
        "SELECT stock_code, strategy_id, signal_date, entry_date, entry_price, signal_payload_json "
        "FROM live_signal_registry "
        "WHERE action=%s AND signal_date=%s",
        ("BUY_CANDIDATE", today),
    ).fetchall()

    registered = 0
    for r in rows:
        code = r[0]
        strategy_id = r[1]
        signal_date = r[2]
        entry_date = r[3] or pe.next_trading_day(signal_date)
        payload = json.loads(r[5] or "{}")
        stock_name = payload.get("stock_name", code)
        snapshot = {
            "strategy_id": strategy_id,
            "stock_name": stock_name,
            "entry_date": entry_date,
            "entry_price": r[4],
            **payload,
        }
        _, inserted = pe.queue_order(
            conn,
            strategy=STRATEGY,
            stock_code=code,
            stock_name=stock_name,
            side="buy",
            signal_date=signal_date,
            snapshot=snapshot,
            params={"strategy_id": strategy_id, "position_size_krw": POSITION_SIZE_KRW},
            strategy_version="sc_paper_v1",
        )
        if inserted:
            registered += 1
            logger.info(f"[sc_paper] 주문 등록: {code} {stock_name} signal={signal_date}")

    if close_conn:
        conn.commit()
        conn.close()
    return registered


def _check_exit_signals(conn, today: str) -> int:
    """보유 종목 중 손절/익절/기간 만료 → paper_order_queue에 SELL 등록."""
    pe.ensure(conn)
    rows = conn.execute(
        "SELECT id, stock_code, stock_name, buy_price, entry_date "
        "FROM peak_holding WHERE strategy=%s AND is_active=1",
        (STRATEGY,),
    ).fetchall()

    queued = 0
    for r in rows:
        h_id, code, name, buy_price, entry_date = r[0], r[1], r[2], float(r[3] or 0), r[4] or today
        if buy_price <= 0:
            continue
        # 현재가 조회
        price_row = conn.execute(
            "SELECT close FROM price_history "
            "WHERE stock_code=%s AND substr(date,1,10)<=%s AND close>0 "
            "ORDER BY date DESC LIMIT 1",
            (code, today),
        ).fetchone()
        if not price_row:
            continue
        current = float(price_row[0])
        pct = (current - buy_price) / buy_price

        # 보유일
        try:
            hold_days = (date.fromisoformat(today) - date.fromisoformat(str(entry_date)[:10])).days
        except Exception:
            hold_days = 0

        reason = None
        if pct <= STOP_LOSS_PCT:
            reason = f"stop_loss({pct:.1%})"
        elif pct >= TAKE_PROFIT_PCT:
            reason = f"take_profit({pct:.1%})"
        elif hold_days >= MAX_HOLD_DAYS:
            reason = f"max_hold({hold_days}d)"

        if reason:
            _, inserted = pe.queue_order(
                conn,
                strategy=STRATEGY,
                stock_code=code,
                stock_name=name,
                side="sell",
                signal_date=today,
                snapshot={"exit_reason": reason, "current_price": current, "pct": pct},
                params={"exit": reason},
                strategy_version="sc_paper_v1",
                holding_id=int(h_id),
            )
            if inserted:
                queued += 1
                logger.info(f"[sc_paper] 매도 신호: {code} {name} {reason}")

    return queued


def fill_pending(conn=None, as_of_date: str | None = None) -> dict:
    """09:10 호출: pending 주문을 as_of_date의 시가로 체결."""
    close_conn = conn is None
    if conn is None:
        conn = _conn()
    today = as_of_date or date.today().isoformat()
    pe.ensure(conn)

    # 매도 종료 신호 먼저 큐에 넣기
    sell_queued = _check_exit_signals(conn, today)
    if sell_queued:
        logger.info(f"[sc_paper] 매도 신호 {sell_queued}건 등록")

    def size_buy(order, open_price, fill_day):
        cash = float(available_cash(conn, STRATEGY, pe.PAPER_INITIAL_CASH))
        active = conn.execute(
            "SELECT COUNT(*) FROM peak_holding WHERE strategy=%s AND is_active=1",
            (STRATEGY,),
        ).fetchone()[0]
        if active >= MAX_POSITIONS:
            return 0, "max_positions_reached"
        if cash < open_price:
            return 0, "insufficient_cash"
        budget = min(cash, POSITION_SIZE_KRW)
        qty = int(budget // open_price)
        return (qty, None) if qty > 0 else (0, "zero_qty")

    def mkt_cap_of(code, fill_day, price):
        return _mkt_cap_of(conn, code, fill_day, price)

    def buy_reason(order):
        snap = json.loads(order.get("signal_snapshot_json") or "{}")
        sid = snap.get("strategy_id", "")
        name = snap.get("stock_name", order.get("stock_code", ""))
        return f"전략센터 BUY신호 [{sid}] {name}"

    result = pe.settle(
        conn, STRATEGY, today,
        size_buy=size_buy,
        mkt_cap_of=mkt_cap_of,
        buy_reason=buy_reason,
    )

    conn.commit()
    logger.info(
        f"[sc_paper] settle {today}: "
        f"bought={len(result['bought'])} sold={len(result['sold'])} "
        f"unfilled={len(result['unfilled'])} cancelled={len(result['cancelled'])} "
        f"carried={len(result['carried'])}"
    )
    if close_conn:
        conn.close()
    return result


def summary(conn=None) -> dict:
    """현재 포지션 요약."""
    close_conn = conn is None
    if conn is None:
        conn = _conn()
    today = date.today().isoformat()
    cash = float(available_cash(conn, STRATEGY, pe.PAPER_INITIAL_CASH))
    mtm = pe.mark_to_market(conn, STRATEGY, today)
    positions = conn.execute(
        "SELECT stock_code, stock_name, buy_price, current_price, quantity, entry_date, profit_pct "
        "FROM peak_holding WHERE strategy=%s AND is_active=1 ORDER BY entry_date DESC",
        (STRATEGY,),
    ).fetchall()
    pos_list = [
        {
            "stock_code": r[0], "stock_name": r[1],
            "buy_price": r[2], "current_price": r[3],
            "quantity": r[4], "entry_date": r[5], "profit_pct": r[6],
        }
        for r in positions
    ]
    recent_orders = conn.execute(
        "SELECT order_id, stock_code, side, signal_date, status, fill_price, qty, reason "
        "FROM paper_order_queue WHERE strategy=%s ORDER BY created_at DESC LIMIT 20",
        (STRATEGY,),
    ).fetchall()
    orders_list = [
        {
            "order_id": r[0], "stock_code": r[1], "side": r[2],
            "signal_date": r[3], "status": r[4],
            "fill_price": r[5], "qty": r[6], "reason": r[7],
        }
        for r in recent_orders
    ]
    if close_conn:
        conn.close()
    return {
        "strategy": STRATEGY,
        "cash_krw": round(cash),
        "mtm_krw": round(mtm),
        "return_pct": round((mtm / pe.PAPER_INITIAL_CASH - 1) * 100, 2),
        "positions": pos_list,
        "recent_orders": orders_list,
    }
