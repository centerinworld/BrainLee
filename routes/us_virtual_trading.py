from __future__ import annotations

import os
import sqlite3
from datetime import datetime

import config
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from db_utils import connect_stock_db


router = APIRouter(prefix="/api/us-virtual", tags=["us-virtual-trading"])
def _conn() -> sqlite3.Connection:
    # Use the same configured primary database as the collection pipeline.
    # Direct sqlite3.connect("stock.db") is router-dependent and can otherwise
    # drift to the legacy SQLite copy when an operator runs code manually.
    c = connect_stock_db(timeout=30)
    c.row_factory = sqlite3.Row
    return c


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _initial_cash() -> float:
    return float(os.getenv("US_PAPER_INITIAL_CASH_USD", "100000"))


def _init_tables() -> None:
    c = _conn()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS us_paper_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            ticker TEXT NOT NULL,
            side TEXT NOT NULL,
            qty INTEGER NOT NULL,
            req_price REAL,
            fill_price REAL NOT NULL,
            status TEXT NOT NULL,
            reason TEXT,
            amount_usd REAL NOT NULL,
            strategy_key TEXT,
            signal_snapshot TEXT,
            price_as_of TEXT,
            mode TEXT NOT NULL DEFAULT 'PAPER'
        );

        CREATE TABLE IF NOT EXISTS us_paper_positions (
            ticker TEXT PRIMARY KEY,
            qty INTEGER NOT NULL,
            avg_price REAL NOT NULL,
            updated_at TEXT NOT NULL,
            strategy_key TEXT
        );

        CREATE TABLE IF NOT EXISTS us_paper_realized (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            ticker TEXT NOT NULL,
            qty INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL NOT NULL,
            pnl_usd REAL NOT NULL,
            strategy_key TEXT
        );

        CREATE TABLE IF NOT EXISTS us_paper_cash_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            delta_usd REAL NOT NULL,
            balance_after REAL NOT NULL,
            reason TEXT,
            ref_order_id INTEGER
        );

        -- A completed market-date run is the idempotency boundary for the
        -- scheduled paper portfolio.  A service restart must not buy twice.
        CREATE TABLE IF NOT EXISTS us_paper_run_log (
            market_date TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT NOT NULL,
            result_json TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_us_paper_orders_ts ON us_paper_orders(ts DESC);
        CREATE INDEX IF NOT EXISTS idx_us_paper_realized_ts ON us_paper_realized(ts DESC);
        -- Candidate freshness is based on COUNT(DISTINCT ticker) by market
        -- date.  The primary key is ticker-first, so this separate index avoids
        -- a full temporary grouping of the multi-million-row price history.
        CREATE INDEX IF NOT EXISTS idx_us_price_date_ticker ON us_price_history(date DESC, ticker);
        """
    )
    if c.execute("SELECT COUNT(*) FROM us_paper_cash_ledger").fetchone()[0] == 0:
        c.execute(
            "INSERT INTO us_paper_cash_ledger(ts, delta_usd, balance_after, reason) VALUES(?,?,?,?)",
            (_now(), _initial_cash(), _initial_cash(), "initial_cash"),
        )
    c.commit()
    c.close()


_init_tables()


class USPaperOrderIn(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=12)
    side: str = Field(..., pattern="^(buy|sell)$")
    qty: int | None = Field(default=None, ge=1)
    amount_usd: float | None = Field(default=None, gt=0)
    limit_price: float | None = Field(default=None, gt=0)
    strategy_key: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def _qty_or_amount(self):
        if self.qty is None and self.amount_usd is None:
            raise ValueError("qty 또는 amount_usd 중 하나가 필요합니다")
        return self


def _latest_broad_us_date(c: sqlite3.Connection) -> str | None:
    """Return the latest session that covers the active US price universe.

    The universe changes as listings, delistings, and ticker changes occur, so
    a fixed ticker count would eventually either accept partial data or block a
    valid session forever. The coverage floor is a configurable ratio of the
    best recent local universe coverage instead.
    """
    ratio = min(1.0, max(0.5, float(os.getenv("US_PAPER_MIN_COVERAGE_RATIO", "0.95"))))
    reference = c.execute(
        """
        SELECT MAX(ticker_count)
        FROM (
          SELECT COUNT(DISTINCT ticker) AS ticker_count
          FROM us_price_history
          GROUP BY date
        ) coverage
        """
    ).fetchone()
    max_coverage = int(reference[0] or 0) if reference else 0
    minimum_coverage = max(1, int(max_coverage * ratio + 0.9999))
    row = c.execute(
        """
        SELECT date
        FROM (
          SELECT date, COUNT(DISTINCT ticker) AS tickers
          FROM us_price_history
          GROUP BY date
        )
        WHERE tickers >= ?
        ORDER BY date DESC
        LIMIT 1
        """,
        (minimum_coverage,),
    ).fetchone()
    return row[0] if row else None


def _latest_us_price_coverage(c: sqlite3.Connection) -> dict:
    row = c.execute(
        """
        SELECT date, COUNT(DISTINCT ticker) AS ticker_count
        FROM us_price_history
        GROUP BY date
        ORDER BY date DESC
        LIMIT 1
        """
    ).fetchone()
    reference = c.execute(
        """
        SELECT MAX(ticker_count)
        FROM (
          SELECT COUNT(DISTINCT ticker) AS ticker_count
          FROM us_price_history
          GROUP BY date
        ) coverage
        """
    ).fetchone()
    ratio = min(1.0, max(0.5, float(os.getenv("US_PAPER_MIN_COVERAGE_RATIO", "0.95"))))
    maximum = int(reference[0] or 0) if reference else 0
    minimum = max(1, int(maximum * ratio + 0.9999))
    count = int(row["ticker_count"] or 0) if row else 0
    return {
        "date": str(row["date"])[:10] if row else None,
        "ticker_count": count,
        "reference_ticker_count": maximum,
        "minimum_required": minimum,
        "coverage_ratio": round(count / maximum, 4) if maximum else 0.0,
        "ready": bool(row and count >= minimum),
    }


def _latest_price(c: sqlite3.Connection, ticker: str) -> tuple[float | None, str | None]:
    row = c.execute(
        """
        SELECT close, date
        FROM us_price_history
        WHERE ticker=? AND close IS NOT NULL AND close>0
        ORDER BY date DESC
        LIMIT 1
        """,
        (ticker,),
    ).fetchone()
    if not row:
        return None, None
    return float(row["close"]), str(row["date"])[:10]


def _latest_kr_price(c: sqlite3.Connection, stock_code: str) -> tuple[float | None, str | None]:
    row = c.execute(
        """
        SELECT close, date
        FROM price_history
        WHERE stock_code=? AND close IS NOT NULL AND close>0
        ORDER BY date DESC
        LIMIT 1
        """,
        (stock_code,),
    ).fetchone()
    if not row:
        return None, None
    return float(row["close"]), str(row["date"])[:10]


def _latest_usdkrw(c: sqlite3.Connection) -> dict:
    row = c.execute(
        """
        SELECT close AS value, date
        FROM price_history
        WHERE stock_code='USDKRW=X' AND close IS NOT NULL AND close>0
        ORDER BY date DESC
        LIMIT 1
        """
    ).fetchone()
    if row:
        return {
            "rate": float(row["value"]),
            "date": str(row["date"])[:10],
            "source": "price_history:USDKRW=X",
        }
    row = c.execute(
        """
        SELECT value, date
        FROM global_macro_data
        WHERE indicator_code='KR_USD_KRW' AND value IS NOT NULL AND value>0
        ORDER BY date DESC
        LIMIT 1
        """
    ).fetchone()
    if row:
        return {
            "rate": float(row["value"]),
            "date": str(row["date"])[:10],
            "source": "global_macro_data:KR_USD_KRW",
        }
    return {"rate": 1350.0, "date": None, "source": "fallback:1350"}


def _cash_balance(c: sqlite3.Connection) -> float:
    row = c.execute(
        "SELECT balance_after FROM us_paper_cash_ledger ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return float(row[0]) if row else _initial_cash()


def _write_cash(c: sqlite3.Connection, delta: float, reason: str, ref_order_id: int | None = None) -> float:
    balance = _cash_balance(c) + float(delta)
    c.execute(
        "INSERT INTO us_paper_cash_ledger(ts, delta_usd, balance_after, reason, ref_order_id) VALUES(?,?,?,?,?)",
        (_now(), float(delta), balance, reason, ref_order_id),
    )
    return balance


def _insert_and_get_id(c: sqlite3.Connection, statement: str, params: tuple, id_column: str = "id") -> int:
    """Return the generated paper-order ID on both SQLite and PostgreSQL."""
    if config.IS_POSTGRES:
        row = c.execute(f"{statement.rstrip()} RETURNING {id_column}", params).fetchone()
        return int(row[0])
    cursor = c.execute(statement, params)
    if cursor.lastrowid is None:
        raise RuntimeError(f"generated {id_column} was not returned")
    return int(cursor.lastrowid)


def _signal_snapshot(c: sqlite3.Connection, ticker: str) -> dict:
    row = c.execute(
        """
        SELECT f.ticker, m.company_name AS name, f.as_of_date, f.price, f.market_cap,
               f.sector, f.industry, f.return_3m, f.return_6m, f.return_1y,
               f.rs_score, f.above_200ma, f.total_score, f.system_action,
               f.op_margin, f.roe, f.per, f.pbr, f.fcf_yield, f.debt_to_equity
        FROM us_factor_snapshot f
        LEFT JOIN us_stock_meta m ON m.ticker=f.ticker
        WHERE f.ticker=?
        """,
        (ticker,),
    ).fetchone()
    return dict(row) if row else {}


def _risk_check(c: sqlite3.Connection, ticker: str, side: str, price_as_of: str | None, px: float) -> list[str]:
    reasons: list[str] = []
    broad = _latest_broad_us_date(c)
    if broad and price_as_of and price_as_of < broad:
        reasons.append(f"가격 데이터 지연: {ticker} {price_as_of} < broad {broad}")
    if not px or px <= 0:
        reasons.append("현재가 없음")
    snap = _signal_snapshot(c, ticker)
    if side == "buy":
        if not snap:
            reasons.append("팩터 스냅샷 없음")
        if snap and float(snap.get("market_cap") or 0) < 1_000_000_000:
            reasons.append("시총 10억 달러 미만")
        if snap and int(snap.get("above_200ma") or 0) != 1:
            reasons.append("200일선 하회")
    return reasons


@router.post("/order")
def place_us_paper_order(order: USPaperOrderIn):
    ticker = (order.ticker or "").strip().upper()
    c = _conn()
    try:
        px, price_as_of = _latest_price(c, ticker)
        if not px:
            raise HTTPException(status_code=400, detail="미국 종목 현재가를 찾을 수 없습니다")
        fill_price = float(order.limit_price or px)
        if fill_price <= 0:
            raise HTTPException(status_code=400, detail="체결가 오류")

        qty = int(order.qty or 0)
        if qty <= 0 and order.amount_usd:
            qty = int(float(order.amount_usd) // fill_price)
        if qty <= 0:
            raise HTTPException(status_code=400, detail="주문 가능 수량이 0입니다")

        reasons = _risk_check(c, ticker, order.side, price_as_of, fill_price)
        if reasons:
            raise HTTPException(status_code=400, detail="; ".join(reasons))

        pos = c.execute("SELECT qty, avg_price FROM us_paper_positions WHERE ticker=?", (ticker,)).fetchone()
        amount = fill_price * qty

        if order.side == "buy":
            cash = _cash_balance(c)
            if cash < amount:
                raise HTTPException(status_code=400, detail=f"달러 현금 부족: 필요 ${amount:,.2f} > 보유 ${cash:,.2f}")
            if pos:
                old_qty = int(pos["qty"])
                old_avg = float(pos["avg_price"])
                new_qty = old_qty + qty
                new_avg = ((old_qty * old_avg) + (qty * fill_price)) / new_qty
                c.execute(
                    "UPDATE us_paper_positions SET qty=?, avg_price=?, updated_at=?, strategy_key=COALESCE(?, strategy_key) WHERE ticker=?",
                    (new_qty, new_avg, _now(), order.strategy_key, ticker),
                )
            else:
                c.execute(
                    "INSERT INTO us_paper_positions(ticker, qty, avg_price, updated_at, strategy_key) VALUES(?,?,?,?,?)",
                    (ticker, qty, fill_price, _now(), order.strategy_key),
                )
        else:
            if not pos or int(pos["qty"]) < qty:
                raise HTTPException(status_code=400, detail="매도수량이 보유수량보다 큽니다")
            old_qty = int(pos["qty"])
            old_avg = float(pos["avg_price"])
            pnl = (fill_price - old_avg) * qty
            remain = old_qty - qty
            if remain == 0:
                c.execute("DELETE FROM us_paper_positions WHERE ticker=?", (ticker,))
            else:
                c.execute("UPDATE us_paper_positions SET qty=?, updated_at=? WHERE ticker=?", (remain, _now(), ticker))
            c.execute(
                "INSERT INTO us_paper_realized(ts, ticker, qty, entry_price, exit_price, pnl_usd, strategy_key) VALUES(?,?,?,?,?,?,?)",
                (_now(), ticker, qty, old_avg, fill_price, pnl, order.strategy_key),
            )

        import json

        snap = _signal_snapshot(c, ticker)
        order_id = _insert_and_get_id(
            c,
            """
            INSERT INTO us_paper_orders
            (ts, ticker, side, qty, req_price, fill_price, status, reason, amount_usd, strategy_key, signal_snapshot, price_as_of, mode)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                _now(), ticker, order.side, qty, order.limit_price, fill_price, "FILLED", "paper",
                amount, order.strategy_key, json.dumps(snap, ensure_ascii=False), price_as_of, "PAPER",
            ),
        )
        _write_cash(c, -amount if order.side == "buy" else amount, f"{order.side}:{ticker}", order_id)
        c.commit()
        return {
            "ok": True,
            "order_id": order_id,
            "ticker": ticker,
            "side": order.side,
            "qty": qty,
            "fill_price": round(fill_price, 4),
            "amount_usd": round(amount, 2),
            "price_as_of": price_as_of,
        }
    finally:
        c.close()


@router.get("/positions")
def us_paper_positions():
    c = _conn()
    try:
        rows = c.execute("SELECT * FROM us_paper_positions ORDER BY ticker").fetchall()
        out = []
        total_eval = 0.0
        total_cost = 0.0
        for r in rows:
            ticker = r["ticker"]
            qty = int(r["qty"])
            avg = float(r["avg_price"])
            px, as_of = _latest_price(c, ticker)
            cur = float(px or avg)
            cost = avg * qty
            eval_ = cur * qty
            total_cost += cost
            total_eval += eval_
            snap = _signal_snapshot(c, ticker)
            out.append({
                "ticker": ticker,
                "name": snap.get("name") or ticker,
                "qty": qty,
                "avg_price": round(avg, 4),
                "current_price": round(cur, 4),
                "market_value_usd": round(eval_, 2),
                "cost_usd": round(cost, 2),
                "unrealized_pnl_usd": round(eval_ - cost, 2),
                "unrealized_pct": round(((cur - avg) / avg * 100.0), 2) if avg else 0.0,
                "price_as_of": as_of,
                "strategy_key": r["strategy_key"],
                "sector": snap.get("sector"),
                "system_action": snap.get("system_action"),
                "total_score": snap.get("total_score"),
                "updated_at": r["updated_at"],
            })
        realized = c.execute("SELECT COALESCE(SUM(pnl_usd),0) FROM us_paper_realized").fetchone()[0]
        cash = _cash_balance(c)
        return {
            "currency": "USD",
            "cash_usd": round(cash, 2),
            "positions": out,
            "summary": {
                "position_count": len(out),
                "total_cost_usd": round(total_cost, 2),
                "total_eval_usd": round(total_eval, 2),
                "unrealized_pnl_usd": round(total_eval - total_cost, 2),
                "realized_pnl_usd": round(float(realized or 0), 2),
                "equity_usd": round(cash + total_eval, 2),
            },
        }
    finally:
        c.close()


@router.get("/combined-summary")
def combined_virtual_summary():
    """KRW-converted summary across Korean virtual holdings and US paper ledger."""
    c = _conn()
    try:
        fx = _latest_usdkrw(c)
        usdkrw = float(fx["rate"])

        kr_rows = c.execute(
            """
            SELECT id, strategy, stock_code, stock_name, buy_price, current_price, quantity, profit_pct
            FROM peak_holding
            WHERE is_active=1 AND COALESCE(quantity,0)>0
              AND stock_code IS NOT NULL AND TRIM(stock_code)!=''
              AND LENGTH(TRIM(stock_code))=6
            """
        ).fetchall()
        kr_cost = 0.0
        kr_eval = 0.0
        kr_stale = 0
        kr_strategy: dict[str, dict] = {}
        kr_latest_dates: list[str] = []
        for r in kr_rows:
            qty = int(r["quantity"] or 0)
            buy = float(r["buy_price"] or 0)
            fallback_cur = float(r["current_price"] or buy or 0)
            latest, as_of = _latest_kr_price(c, str(r["stock_code"]).strip())
            cur = float(latest or fallback_cur)
            if not latest:
                kr_stale += 1
            if as_of:
                kr_latest_dates.append(as_of)
            cost = buy * qty
            eval_ = cur * qty
            kr_cost += cost
            kr_eval += eval_
            key = r["strategy"] or "unknown"
            item = kr_strategy.setdefault(key, {"positions": 0, "cost_krw": 0.0, "eval_krw": 0.0})
            item["positions"] += 1
            item["cost_krw"] += cost
            item["eval_krw"] += eval_

        us = us_paper_positions()
        us_summary = us.get("summary", {})
        us_cash = float(us.get("cash_usd") or 0)
        us_eval = float(us_summary.get("total_eval_usd") or 0)
        us_cost = float(us_summary.get("total_cost_usd") or 0)
        us_equity = float(us_summary.get("equity_usd") or 0)
        us_unrealized = float(us_summary.get("unrealized_pnl_usd") or 0)
        us_realized = float(us_summary.get("realized_pnl_usd") or 0)

        kr_pnl = kr_eval - kr_cost
        total_equity_krw = kr_eval + (us_equity * usdkrw)
        total_unrealized_krw = kr_pnl + (us_unrealized * usdkrw)

        return {
            "currency": "KRW",
            "fx": fx,
            "kr": {
                "position_count": len(kr_rows),
                "cost_krw": round(kr_cost),
                "eval_krw": round(kr_eval),
                "unrealized_pnl_krw": round(kr_pnl),
                "unrealized_pct": round((kr_pnl / kr_cost * 100.0), 2) if kr_cost else 0.0,
                "stale_price_count": kr_stale,
                "latest_price_date": max(kr_latest_dates) if kr_latest_dates else None,
                "by_strategy": [
                    {
                        "strategy": k,
                        "positions": v["positions"],
                        "cost_krw": round(v["cost_krw"]),
                        "eval_krw": round(v["eval_krw"]),
                        "unrealized_pnl_krw": round(v["eval_krw"] - v["cost_krw"]),
                    }
                    for k, v in sorted(kr_strategy.items())
                ],
            },
            "us": {
                "position_count": int(us_summary.get("position_count") or 0),
                "cash_usd": round(us_cash, 2),
                "cash_krw": round(us_cash * usdkrw),
                "cost_usd": round(us_cost, 2),
                "cost_krw": round(us_cost * usdkrw),
                "eval_usd": round(us_eval, 2),
                "eval_krw": round(us_eval * usdkrw),
                "equity_usd": round(us_equity, 2),
                "equity_krw": round(us_equity * usdkrw),
                "unrealized_pnl_usd": round(us_unrealized, 2),
                "unrealized_pnl_krw": round(us_unrealized * usdkrw),
                "realized_pnl_usd": round(us_realized, 2),
                "realized_pnl_krw": round(us_realized * usdkrw),
            },
            "total": {
                "equity_krw": round(total_equity_krw),
                "unrealized_pnl_krw": round(total_unrealized_krw),
                "unrealized_pct_on_cost": round(
                    total_unrealized_krw / (kr_cost + us_cost * usdkrw) * 100.0, 2
                ) if (kr_cost + us_cost * usdkrw) else 0.0,
                "position_count": len(kr_rows) + int(us_summary.get("position_count") or 0),
            },
        }
    finally:
        c.close()


@router.get("/orders")
def us_paper_orders(limit: int = Query(100, ge=1, le=1000)):
    c = _conn()
    try:
        rows = c.execute("SELECT * FROM us_paper_orders ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


@router.get("/candidates")
def us_virtual_candidates(limit: int = Query(50, ge=1, le=200)):
    c = _conn()
    try:
        broad = _latest_broad_us_date(c)
        rows = c.execute(
            """
            SELECT f.ticker, m.company_name AS name, f.as_of_date, f.price, f.market_cap,
                   f.sector, f.industry, f.return_3m, f.return_6m, f.rs_score,
                   f.op_margin, f.roe, f.per, f.pbr, f.total_score, f.system_action
            FROM us_factor_snapshot f
            LEFT JOIN us_stock_meta m ON m.ticker=f.ticker
            WHERE COALESCE(f.market_cap,0) >= 5000000000
              AND COALESCE(f.price,0) >= 5
              AND COALESCE(f.above_200ma,0) = 1
              AND COALESCE(f.op_margin,-999) > 0
              AND f.as_of_date >= COALESCE(?, f.as_of_date)
            ORDER BY COALESCE(f.total_score,0) DESC, COALESCE(f.rs_score,0) DESC, COALESCE(f.market_cap,0) DESC
            LIMIT ?
            """,
            (broad, limit),
        ).fetchall()
        return {"as_of": broad, "currency": "USD", "candidates": [dict(r) for r in rows]}
    finally:
        c.close()


@router.post("/execute-candidates")
def execute_us_virtual_candidates(
    limit: int = Query(5, ge=1, le=20),
    allocation_usd: float = Query(10000, gt=0, le=50000),
):
    """Buy top US virtual candidates that are not already held.

    This is the US analogue of strategy-driven virtual trading: it uses the
    candidate feed and records fills in the dedicated USD paper ledger.
    """
    c = _conn()
    try:
        held = {
            str(r[0]).upper()
            for r in c.execute("SELECT ticker FROM us_paper_positions WHERE qty>0").fetchall()
        }
    finally:
        c.close()

    feed = us_virtual_candidates(limit=max(limit * 3, limit))
    executed = []
    skipped = []
    for row in feed.get("candidates", []):
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            continue
        if ticker in held:
            skipped.append({"ticker": ticker, "reason": "already_held"})
            continue
        try:
            result = place_us_paper_order(
                USPaperOrderIn(
                    ticker=ticker,
                    side="buy",
                    amount_usd=allocation_usd,
                    strategy_key="us_virtual_candidates",
                )
            )
            executed.append(result)
            held.add(ticker)
            if len(executed) >= limit:
                break
        except HTTPException as exc:
            skipped.append({"ticker": ticker, "reason": exc.detail})

    return {
        "ok": True,
        "as_of": feed.get("as_of"),
        "allocation_usd": allocation_usd,
        "executed_count": len(executed),
        "executed": executed,
        "skipped": skipped[:20],
    }


def run_us_virtual_daily_rebalance(
    max_positions: int = 5,
    allocation_usd: float = 10000.0,
    expected_market_date: str | None = None,
    force: bool = False,
) -> dict:
    """Run one idempotent, USD-only paper rebalance for the latest broad US session.

    This deliberately operates only on the dedicated US paper ledger.  It is not
    connected to KIS or any live broker route. Existing positions exit on an
    explicit SELL factor signal or when they leave the ranked candidate buffer;
    fresh BUY candidates fill newly available slots after the exit pass.
    """
    max_positions = max(1, min(int(max_positions), 20))
    allocation_usd = max(100.0, min(float(allocation_usd), 50000.0))

    c = _conn()
    try:
        market_date = _latest_broad_us_date(c)
        if not market_date:
            return {"ok": False, "reason": "latest broad US session is unavailable"}
        if expected_market_date and market_date != expected_market_date:
            return {
                "ok": False,
                "reason": "US price/factor collection has not reached the expected session",
                "market_date": market_date,
                "expected_market_date": expected_market_date,
            }
        prior = c.execute(
            "SELECT status, result_json FROM us_paper_run_log WHERE market_date=?",
            (market_date,),
        ).fetchone()
        if prior and prior["status"] == "success" and not force:
            return {
                "ok": True,
                "skipped": True,
                "reason": "already completed for this market session",
                "market_date": market_date,
            }
        c.execute(
            "INSERT OR REPLACE INTO us_paper_run_log(market_date, started_at, completed_at, status, result_json) VALUES(?,?,?,?,?)",
            (market_date, _now(), None, "running", None),
        )
        positions = [dict(row) for row in c.execute(
            "SELECT ticker, qty, strategy_key FROM us_paper_positions WHERE qty>0 ORDER BY ticker"
        ).fetchall()]
        c.commit()
    finally:
        c.close()

    # Keep a wider ranked buffer. A holding that falls out of this list is not
    # silently carried forever just because it still says HOLD/BUY.
    candidate_feed = us_virtual_candidates(limit=max_positions * 3)
    ranked_tickers = {
        str(row.get("ticker") or "").upper()
        for row in candidate_feed.get("candidates", [])
        if row.get("ticker")
    }

    sold: list[dict] = []
    skipped: list[dict] = []
    for position in positions:
        ticker = str(position["ticker"]).upper()
        c = _conn()
        try:
            snapshot = _signal_snapshot(c, ticker)
        finally:
            c.close()
        action = str(snapshot.get("system_action") or "").upper()
        exit_reason = None
        if action == "SELL":
            exit_reason = "factor_sell"
        elif ticker not in ranked_tickers:
            exit_reason = "ranked_candidate_buffer_exit"
        if not exit_reason:
            continue
        try:
            order = place_us_paper_order(USPaperOrderIn(
                ticker=ticker,
                side="sell",
                qty=int(position["qty"]),
                strategy_key=position.get("strategy_key") or "us_virtual_candidates",
            ))
            order["rebalance_reason"] = exit_reason
            sold.append(order)
        except HTTPException as exc:
            skipped.append({"ticker": ticker, "side": "sell", "reason": str(exc.detail)})

    c = _conn()
    try:
        held = {
            str(row[0]).upper()
            for row in c.execute("SELECT ticker FROM us_paper_positions WHERE qty>0").fetchall()
        }
    finally:
        c.close()
    slots = max(0, max_positions - len(held))
    bought: list[dict] = []
    if slots:
        for row in candidate_feed.get("candidates", []):
            ticker = str(row.get("ticker") or "").upper()
            if not ticker or ticker in held:
                continue
            try:
                bought.append(place_us_paper_order(USPaperOrderIn(
                    ticker=ticker,
                    side="buy",
                    amount_usd=allocation_usd,
                    strategy_key="us_virtual_candidates",
                )))
                held.add(ticker)
                if len(bought) >= slots:
                    break
            except HTTPException as exc:
                skipped.append({"ticker": ticker, "side": "buy", "reason": str(exc.detail)})

    result = {
        "ok": True,
        "market_date": market_date,
        "sold": sold,
        "bought": bought,
        "skipped_orders": skipped[:50],
        "position_count_after": len(held),
        "forced": bool(force),
    }
    c = _conn()
    try:
        import json
        c.execute(
            "UPDATE us_paper_run_log SET completed_at=?, status=?, result_json=? WHERE market_date=?",
            (_now(), "success", json.dumps(result, ensure_ascii=False), market_date),
        )
        c.commit()
    finally:
        c.close()
    return result


@router.get("/status")
def us_virtual_status():
    """Expose the daily-run boundary so an idle paper portfolio is explainable."""
    c = _conn()
    try:
        latest = c.execute(
            """
            SELECT market_date, started_at, completed_at, status, result_json
            FROM us_paper_run_log
            ORDER BY market_date DESC
            LIMIT 1
            """
        ).fetchone()
        broad_date = _latest_broad_us_date(c)
        coverage = _latest_us_price_coverage(c)
        payload = dict(latest) if latest else None
        if payload and payload.get("result_json"):
            import json
            try:
                payload["result"] = json.loads(payload.pop("result_json"))
            except (TypeError, ValueError):
                payload.pop("result_json", None)
        return {
            "ok": True,
            "latest_broad_us_date": broad_date,
            "latest_price_coverage": coverage,
            "latest_run": payload,
            "notice": "미국 가상매매는 미국 장 마감 후 완료된 세션당 한 번만 리밸런싱합니다. 상위 후보 버퍼 이탈 또는 SELL 신호에서 교체합니다.",
        }
    finally:
        c.close()


@router.post("/run-daily-rebalance")
def run_us_virtual_daily_rebalance_api(
    max_positions: int = Query(5, ge=1, le=20),
    allocation_usd: float = Query(10000, gt=0, le=50000),
    force: bool = Query(False, description="같은 기준일의 로직 수정 후 가상 리밸런싱을 재검증"),
):
    """Manually trigger the same idempotent paper-only daily rebalance."""
    return run_us_virtual_daily_rebalance(max_positions, allocation_usd, force=force)
