"""백테스트와 같은 체결 규칙의 종이운용(paper) 공통 원장 — 2026-09-28.

선택 백테스트는 "D일 종가 신호 → D+1 시가 체결"인데, 기존 가상운용(v_gc·v_contract_momentum)은
장중 20분마다 그 순간의 현재가로 즉시 사고팔아 성과 비교가 성립하지 않았다. 이 모듈은 모든
paper 전략이 공유하는 절차를 고정한다.

  1. D일 장 마감 후 신호 확정 → paper_order_queue에 불변 스냅샷과 함께 기록(pending)
  2. 다음 거래일(D+1) 시가로만 체결 — 시가가 없으면 종가로 대체하지 않고 미체결
  3. 체결 직전 거래정지·관리종목·가격 오염·기업행위 veto 재검사
  4. 수수료·세금·슬리피지는 백테스트와 같은 backtest_common._tx_cost 비율로 차감
  5. 주문·체결·취소 사유를 모두 저장

전략별 신호 규칙은 paper_adapters.py에 있고, 파라미터는 선택된 전략센터 suite의 run spec에서
직접 읽는다(백테스트와 paper 파라미터가 구조적으로 같아지도록).
실전 자동주문과는 무관하다 — 이 원장은 가상 계좌(virtual_cash_ledger)에만 기록한다.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timedelta

from backtest_common import _tx_cost, FEE_PER_LEG, SELL_TAX

logger = logging.getLogger(__name__)

DDL = """
CREATE TABLE IF NOT EXISTS paper_order_queue (
    order_id TEXT PRIMARY KEY,
    strategy TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    side TEXT NOT NULL,
    signal_date TEXT NOT NULL,
    planned_fill_date TEXT,
    holding_id INTEGER,
    signal_snapshot_json TEXT NOT NULL,
    params_hash TEXT,
    strategy_version TEXT,
    status TEXT NOT NULL,
    fill_date TEXT,
    fill_price REAL,
    qty INTEGER,
    fee_krw REAL,
    tax_krw REAL,
    slippage_krw REAL,
    reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""
INDEX_DDL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_paper_order_signal ON paper_order_queue(strategy,stock_code,side,signal_date)",
    "CREATE INDEX IF NOT EXISTS ix_paper_order_status ON paper_order_queue(strategy,status)",
)

PAPER_INITIAL_CASH = 100_000_000.0


def ensure(conn) -> None:
    conn.execute(DDL)
    for ddl in INDEX_DDL:
        conn.execute(ddl)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def next_trading_day(day: str) -> str:
    """day 다음 한국 거래일(휴장일 달력 기준)."""
    from trading_calendar import is_kr_trading_day

    d = date.fromisoformat(str(day)[:10]) + timedelta(days=1)
    for _ in range(20):
        if is_kr_trading_day(d):
            return d.isoformat()
        d += timedelta(days=1)
    raise RuntimeError(f"no trading day within 20 days after {day}")


def params_hash(params: dict) -> str:
    return hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def cost_rates(side: str, mkt_cap_억: float) -> dict:
    """백테스트 _net_profit과 같은 비용율(매수: 수수료+슬리피지, 매도: 수수료+거래세+슬리피지)."""
    buy_r, sell_r = _tx_cost(float(mkt_cap_억 or 500))
    slip = buy_r - FEE_PER_LEG
    if side == "buy":
        return {"fee": FEE_PER_LEG, "tax": 0.0, "slippage": slip}
    return {"fee": FEE_PER_LEG, "tax": SELL_TAX, "slippage": sell_r - FEE_PER_LEG - SELL_TAX}


def queue_order(conn, *, strategy: str, stock_code: str, stock_name: str, side: str,
                signal_date: str, snapshot: dict, params: dict, strategy_version: str,
                holding_id: int | None = None) -> tuple[str, bool]:
    """D일 신호를 주문대기 원장에 기록. 같은 (전략, 종목, 방향, 신호일)은 한 번만 기록된다."""
    ensure(conn)
    order_id = f"{strategy}:{side}:{stock_code}:{signal_date}"
    now = _now()
    cursor = conn.execute(
        """INSERT INTO paper_order_queue
           (order_id,strategy,stock_code,stock_name,side,signal_date,planned_fill_date,holding_id,
            signal_snapshot_json,params_hash,strategy_version,status,reason,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,'pending','signal_close_D',?,?)
           ON CONFLICT(order_id) DO NOTHING""",
        (order_id, strategy, stock_code, stock_name, side, signal_date, next_trading_day(signal_date),
         holding_id, json.dumps(snapshot, ensure_ascii=False, default=str), params_hash(params),
         strategy_version, now, now),
    )
    return order_id, cursor.rowcount > 0


def pending_orders(conn, strategy: str, side: str | None = None) -> list[dict]:
    ensure(conn)
    sql = "SELECT * FROM paper_order_queue WHERE strategy=? AND status='pending'"
    args: list = [strategy]
    if side:
        sql += " AND side=?"
        args.append(side)
    cur = conn.execute(sql + " ORDER BY signal_date, order_id", args)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _close_order(conn, order_id: str, status: str, reason: str, **fields) -> None:
    sets = ["status=?", "reason=?", "updated_at=?"]
    args: list = [status, reason, _now()]
    for key, value in fields.items():
        sets.append(f"{key}=?")
        args.append(value)
    conn.execute(f"UPDATE paper_order_queue SET {', '.join(sets)} WHERE order_id=?", args + [order_id])


def fill_quote(conn, stock_code: str, day: str) -> dict | None:
    row = conn.execute(
        "SELECT open, close, volume FROM price_history WHERE stock_code=? AND substr(date,1,10)=?",
        (stock_code, day),
    ).fetchone()
    if not row:
        return None
    return {"open": float(row[0] or 0), "close": float(row[1] or 0), "volume": float(row[2] or 0)}


def veto_reason(conn, stock_code: str, day: str) -> str | None:
    """체결일 재검사: 거래정지·관리종목·가격 오염·기업행위. None이면 체결 가능."""
    restriction = conn.execute(
        """SELECT is_tradable,is_halted,is_management,source FROM trading_restrictions
           WHERE stock_code=? AND substr(as_of,1,10)<=? ORDER BY as_of DESC LIMIT 1""",
        (stock_code, day),
    ).fetchone()
    if restriction:
        tradable, halted, management, source = restriction
        if halted:
            return f"veto_trading_halt({source})"
        if management:
            return f"veto_management_issue({source})"
        if tradable is not None and int(tradable) == 0:
            return f"veto_not_tradable({source})"
    jump = conn.execute(
        "SELECT classification FROM price_jump_audit WHERE stock_code=? AND event_date=? AND return_usable=0",
        (stock_code, day),
    ).fetchone()
    if jump:
        return f"veto_price_contamination({jump[0]})"
    action = conn.execute(
        """SELECT event_type,adjustment_status FROM corporate_action_events
           WHERE stock_code=? AND event_date=? AND COALESCE(adjustment_status,'')<>'not_price_adjusting'
           LIMIT 1""",
        (stock_code, day),
    ).fetchone()
    if action:
        return f"veto_corporate_action({action[0]}:{action[1]})"
    return None


def mark_to_market(conn, strategy: str, day: str) -> float:
    """가상 계좌 현금 + 보유 종목 day 종가 평가액(백테스트 _gc_equity와 같은 정의)."""
    from virtual_trading_ledger import available_cash

    cash = float(available_cash(conn, strategy, PAPER_INITIAL_CASH))
    value = cash
    for code, qty, buy_price in conn.execute(
        "SELECT stock_code, quantity, buy_price FROM peak_holding WHERE strategy=? AND is_active=1",
        (strategy,),
    ).fetchall():
        row = conn.execute(
            "SELECT close FROM price_history WHERE stock_code=? AND substr(date,1,10)<=? AND close>0 "
            "ORDER BY date DESC LIMIT 1", (code, day),
        ).fetchone()
        value += float(qty or 0) * (float(row[0]) if row else float(buy_price or 0))
    return value


def _book_buy(conn, order: dict, fill_day: str, price: float, qty: int, mkt_cap_억: float,
              reason_text: str) -> int:
    from routes.trend import _ensure_peak_holding_reason_columns, _record_paper_trade

    _ensure_peak_holding_reason_columns(conn)
    occurred_at = f"{fill_day} 09:00:00"
    snapshot = json.loads(order["signal_snapshot_json"] or "{}")
    holding_cursor = conn.execute(
        """INSERT INTO peak_holding
           (stock_code,stock_name,sector,buy_price,current_price,quantity,entry_date,hold_days,profit_pct,
            is_active,strategy,detected_at,updated_at,entry_reason_text,entry_reason_json,entry_reason_updated_at)
           VALUES (?,?,?,?,?,?,?,0,0.0,1,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,?,?,CURRENT_TIMESTAMP)""",
        (order["stock_code"], order["stock_name"], "", price, price, qty, fill_day, order["strategy"],
         reason_text, json.dumps({**snapshot, "order_id": order["order_id"], "signal_date": order["signal_date"]},
                                 ensure_ascii=False, default=str)),
    )
    trade_cursor = conn.execute(
        "INSERT INTO peak_trade (stock_name,tx_type,price,quantity,total_amount,profit,profit_pct,tx_at,strategy) "
        "VALUES (?,?,?,?,?,0,0.0,?,?)",
        (order["stock_name"], "buy", price, qty, round(price * qty), occurred_at, order["strategy"]),
    )
    _record_paper_trade(
        conn, strategy=order["strategy"], side="buy", code=order["stock_code"], name=order["stock_name"],
        holding_id=int(holding_cursor.lastrowid), qty=qty, price=price,
        trade_id=int(trade_cursor.lastrowid), occurred_at=occurred_at,
        cost_rates=cost_rates("buy", mkt_cap_억),
    )
    return int(holding_cursor.lastrowid)


def _book_sell(conn, order: dict, fill_day: str, price: float, mkt_cap_억: float) -> dict | None:
    from routes.trend import _record_paper_trade

    row = conn.execute(
        "SELECT id, quantity, buy_price FROM peak_holding WHERE id=? AND is_active=1",
        (order["holding_id"],),
    ).fetchone()
    if not row:
        return None
    h_id, qty, buy_price = int(row[0]), int(row[1] or 0), float(row[2] or 0)
    occurred_at = f"{fill_day} 09:00:00"
    profit = round((price - buy_price) * qty)
    profit_pct = round((price - buy_price) / buy_price * 100, 2) if buy_price > 0 else 0.0
    conn.execute(
        "UPDATE peak_holding SET is_active=0, sell_price=?, sold_at=?, current_price=?, profit_pct=?, updated_at=? WHERE id=?",
        (price, occurred_at, price, profit_pct, _now(), h_id),
    )
    trade_cursor = conn.execute(
        "INSERT INTO peak_trade (stock_name,tx_type,price,quantity,total_amount,profit,profit_pct,tx_at,strategy) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (order["stock_name"], "sell", price, qty, round(price * qty), profit, profit_pct, occurred_at, order["strategy"]),
    )
    _record_paper_trade(
        conn, strategy=order["strategy"], side="sell", code=order["stock_code"], name=order["stock_name"],
        holding_id=h_id, qty=qty, price=price, trade_id=int(trade_cursor.lastrowid),
        occurred_at=occurred_at, gross_profit=profit, cost_rates=cost_rates("sell", mkt_cap_억),
    )
    return {"qty": qty, "profit_pct": profit_pct}


def settle(conn, strategy: str, as_of: str, *, size_buy, mkt_cap_of, buy_reason) -> dict:
    """as_of 이전 신호의 pending 주문을 계획된 체결일 시가로 처리한다.

    - 매도: 체결일 시세가 없거나 veto면 다음 거래일로 이월(백테스트도 대기 매도를 이월한다)
    - 매수: 체결일 한 번만 시도 — 시가 없음/거래 없음/veto/자금 부족이면 미체결·취소로 닫는다
    size_buy(order, open_price, fill_day) -> (qty, skip_reason)
    """
    result = {"sold": [], "bought": [], "unfilled": [], "cancelled": [], "carried": []}
    for order in pending_orders(conn, strategy, "sell") + pending_orders(conn, strategy, "buy"):
        fill_day = order["planned_fill_date"] or next_trading_day(order["signal_date"])
        if fill_day > as_of:
            continue
        side, code = order["side"], order["stock_code"]
        quote = fill_quote(conn, code, fill_day)
        problem = None
        if quote is None or quote["volume"] <= 0:
            problem = "unfilled_no_trade_on_fill_day"
        elif quote["open"] <= 0:
            problem = "unfilled_no_open_price(close_not_substituted)"
        else:
            problem = veto_reason(conn, code, fill_day)
        if problem:
            if side == "sell":
                # 다음 거래일로 이월 — 원 신호일은 유지하고 계획 체결일만 미룬다.
                conn.execute(
                    "UPDATE paper_order_queue SET planned_fill_date=?, reason=?, updated_at=? WHERE order_id=?",
                    (next_trading_day(fill_day), f"carried:{problem}@{fill_day}", _now(), order["order_id"]),
                )
                result["carried"].append((code, problem))
            else:
                status = "cancelled" if problem.startswith("veto") else "unfilled"
                _close_order(conn, order["order_id"], status, problem, fill_date=fill_day)
                result[status].append((code, problem))
            continue
        price = quote["open"]
        mkt_cap = float(mkt_cap_of(code, fill_day, price) or 500)
        if side == "sell":
            booked = _book_sell(conn, order, fill_day, price, mkt_cap)
            if booked is None:
                _close_order(conn, order["order_id"], "cancelled", "holding_already_closed", fill_date=fill_day)
                result["cancelled"].append((code, "holding_already_closed"))
                continue
            rates = cost_rates("sell", mkt_cap)
            gross = price * booked["qty"]
            _close_order(conn, order["order_id"], "filled", "filled_next_open", fill_date=fill_day,
                         fill_price=price, qty=booked["qty"], fee_krw=round(gross * rates["fee"]),
                         tax_krw=round(gross * rates["tax"]), slippage_krw=round(gross * rates["slippage"]))
            result["sold"].append((code, price, booked["profit_pct"]))
            continue
        qty, skip = size_buy(order, price, fill_day)
        if skip or qty <= 0:
            _close_order(conn, order["order_id"], "unfilled", skip or "zero_qty", fill_date=fill_day)
            result["unfilled"].append((code, skip or "zero_qty"))
            continue
        from routes.trend import _paper_buy_gate

        gate = _paper_buy_gate(code, strategy, qty, price)
        if gate.get("decision") != "BUY_ALLOWED":
            reason = f"risk_gate:{gate.get('decision')}:{';'.join(map(str, gate.get('reasons') or []))[:200]}"
            _close_order(conn, order["order_id"], "cancelled", reason, fill_date=fill_day)
            result["cancelled"].append((code, reason))
            continue
        holding_id = _book_buy(conn, order, fill_day, price, qty, mkt_cap, buy_reason(order))
        rates = cost_rates("buy", mkt_cap)
        gross = price * qty
        _close_order(conn, order["order_id"], "filled", "filled_next_open", fill_date=fill_day,
                     fill_price=price, qty=qty, holding_id=holding_id, fee_krw=round(gross * rates["fee"]),
                     tax_krw=0, slippage_krw=round(gross * rates["slippage"]))
        result["bought"].append((code, price, qty))
    return result
