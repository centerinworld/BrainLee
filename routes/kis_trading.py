from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
import sqlite3
import time
import os
from datetime import datetime

from kis_client import kis_client
import config

router = APIRouter(prefix="/api/kis-trading", tags=["kis-trading"])
DB_PATH = "/Applications/stock_dashboard/stock.db"


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def _init_tables():
    c = _conn()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS kis_paper_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            side TEXT NOT NULL,
            qty INTEGER NOT NULL,
            req_price REAL,
            fill_price REAL,
            status TEXT NOT NULL,
            reason TEXT,
            order_krw REAL,
            mode TEXT NOT NULL DEFAULT 'PAPER'
        );

        CREATE TABLE IF NOT EXISTS kis_paper_positions (
            stock_code TEXT PRIMARY KEY,
            qty INTEGER NOT NULL,
            avg_price REAL NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS kis_paper_realized (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            qty INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL NOT NULL,
            pnl REAL NOT NULL
        );
        """
    )
    c.commit()
    c.close()


_init_tables()


class PaperOrderIn(BaseModel):
    stock_code: str = Field(..., min_length=6, max_length=6)
    side: str = Field(..., pattern="^(buy|sell)$")
    qty: int = Field(..., ge=1)
    limit_price: float | None = Field(default=None, gt=0)


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _trading_mode() -> str:
    # 기본 PAPER 고정. LIVE는 명시적으로만.
    return (os.getenv("KIS_TRADING_MODE", "PAPER") or "PAPER").upper()


def _risk_limits() -> dict:
    return {
        "max_order_krw": float(os.getenv("KIS_PAPER_MAX_ORDER_KRW", "5000000")),
        "max_position_count": int(os.getenv("KIS_PAPER_MAX_POSITION_COUNT", "15")),
        "max_daily_loss_krw": float(os.getenv("KIS_PAPER_MAX_DAILY_LOSS_KRW", "3000000")),
    }


def _latest_price(stock_code: str) -> float | None:
    p = kis_client.get_current_price(stock_code)
    if p and p.get("close"):
        return float(p["close"])
    c = _conn()
    r = c.execute(
        "SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 1",
        (stock_code,),
    ).fetchone()
    c.close()
    return float(r[0]) if r and r[0] else None


def _today_realized_loss(c: sqlite3.Connection) -> float:
    d = datetime.now().strftime("%Y-%m-%d")
    r = c.execute(
        "SELECT COALESCE(SUM(CASE WHEN pnl<0 THEN -pnl ELSE 0 END),0) FROM kis_paper_realized WHERE ts LIKE ?",
        (f"{d}%",),
    ).fetchone()
    return float(r[0] or 0.0)


@router.get("/status")
def trading_status():
    return {
        "mode": _trading_mode(),
        "limits": _risk_limits(),
        "live_order_enabled": _trading_mode() == "LIVE" and os.getenv("KIS_LIVE_ORDER_ENABLE", "false").lower() == "true",
    }


@router.get("/account/summary")
def account_summary():
    snap = kis_client.get_account_snapshot() or {}
    holdings = snap.get("holdings") or []
    summary = snap.get("summary") or {}
    execs = kis_client.get_today_executions() or []
    acc_no = (getattr(config, "KIS_ACCOUNT_NO", "") or os.getenv("KIS_ACCOUNT_NO", "") or "").strip()
    acc_prod = (getattr(config, "KIS_ACCOUNT_PROD", "") or os.getenv("KIS_ACCOUNT_PROD", "") or "").strip()
    acc_masked = acc_no if len(acc_no) < 4 else ("*" * max(0, len(acc_no) - 4) + acc_no[-4:])
    return {
        "mode": _trading_mode(),
        "account_no": acc_no,
        "account_no_masked": acc_masked,
        "account_prod": acc_prod,
        "summary": summary,
        "holdings_count": len(holdings),
        "holdings": holdings,
        "today_executions": execs,
        "updated_at": _now_str(),
    }


@router.post("/paper/order")
def place_paper_order(o: PaperOrderIn):
    if _trading_mode() != "PAPER":
        raise HTTPException(status_code=400, detail="PAPER 모드가 아닙니다. KIS_TRADING_MODE=PAPER 확인 필요")

    px = _latest_price(o.stock_code)
    if not px:
        raise HTTPException(status_code=400, detail="현재가 조회 실패")

    fill_price = float(o.limit_price) if o.limit_price else px
    order_krw = fill_price * o.qty
    limits = _risk_limits()

    c = _conn()
    try:
        # 리스크 1: 주문당 금액 한도
        if order_krw > limits["max_order_krw"]:
            raise HTTPException(status_code=400, detail=f"주문한도 초과: {order_krw:,.0f} > {limits['max_order_krw']:,.0f}")

        # 리스크 2: 일일 손실 한도
        daily_loss = _today_realized_loss(c)
        if daily_loss >= limits["max_daily_loss_krw"]:
            raise HTTPException(status_code=400, detail="일일 손실 한도 초과로 주문 차단")

        pos = c.execute("SELECT qty, avg_price FROM kis_paper_positions WHERE stock_code=?", (o.stock_code,)).fetchone()

        if o.side == "buy":
            pos_cnt = c.execute("SELECT COUNT(*) FROM kis_paper_positions WHERE qty>0").fetchone()[0]
            if not pos and pos_cnt >= limits["max_position_count"]:
                raise HTTPException(status_code=400, detail="최대 보유 종목 수 한도 초과")

            if pos:
                old_qty = int(pos[0]); old_avg = float(pos[1])
                new_qty = old_qty + o.qty
                new_avg = ((old_qty * old_avg) + (o.qty * fill_price)) / new_qty
                c.execute(
                    "UPDATE kis_paper_positions SET qty=?, avg_price=?, updated_at=? WHERE stock_code=?",
                    (new_qty, new_avg, _now_str(), o.stock_code),
                )
            else:
                c.execute(
                    "INSERT INTO kis_paper_positions(stock_code, qty, avg_price, updated_at) VALUES(?,?,?,?)",
                    (o.stock_code, o.qty, fill_price, _now_str()),
                )

        else:  # sell
            if not pos or int(pos[0]) < o.qty:
                raise HTTPException(status_code=400, detail="매도수량이 보유수량보다 큽니다")
            old_qty = int(pos[0]); old_avg = float(pos[1])
            pnl = (fill_price - old_avg) * o.qty
            remain = old_qty - o.qty
            if remain == 0:
                c.execute("DELETE FROM kis_paper_positions WHERE stock_code=?", (o.stock_code,))
            else:
                c.execute(
                    "UPDATE kis_paper_positions SET qty=?, updated_at=? WHERE stock_code=?",
                    (remain, _now_str(), o.stock_code),
                )
            c.execute(
                "INSERT INTO kis_paper_realized(ts, stock_code, qty, entry_price, exit_price, pnl) VALUES(?,?,?,?,?,?)",
                (_now_str(), o.stock_code, o.qty, old_avg, fill_price, pnl),
            )

        c.execute(
            "INSERT INTO kis_paper_orders(ts, stock_code, side, qty, req_price, fill_price, status, reason, order_krw, mode) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (_now_str(), o.stock_code, o.side, o.qty, o.limit_price, fill_price, "FILLED", "paper", order_krw, "PAPER"),
        )
        oid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.commit()
        return {
            "ok": True,
            "order_id": oid,
            "stock_code": o.stock_code,
            "side": o.side,
            "qty": o.qty,
            "fill_price": round(fill_price, 2),
            "order_krw": round(order_krw, 0),
        }
    finally:
        c.close()


@router.get("/paper/orders")
def paper_orders(limit: int = Query(100, ge=1, le=1000)):
    c = _conn()
    rows = c.execute(
        "SELECT * FROM kis_paper_orders ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


@router.get("/paper/positions")
def paper_positions():
    c = _conn()
    pos = c.execute("SELECT * FROM kis_paper_positions ORDER BY stock_code").fetchall()
    out = []
    total_eval = 0.0
    total_cost = 0.0
    for r in pos:
        code = r["stock_code"]
        qty = int(r["qty"])
        avg = float(r["avg_price"])
        px = _latest_price(code) or avg
        eval_ = qty * px
        cost_ = qty * avg
        total_eval += eval_
        total_cost += cost_
        out.append({
            "stock_code": code,
            "qty": qty,
            "avg_price": round(avg, 2),
            "current_price": round(px, 2),
            "unrealized_pnl": round(eval_ - cost_, 0),
            "unrealized_pct": round(((px - avg) / avg * 100.0), 2) if avg else 0.0,
            "updated_at": r["updated_at"],
        })
    c.close()
    return {
        "positions": out,
        "summary": {
            "position_count": len(out),
            "total_eval": round(total_eval, 0),
            "total_cost": round(total_cost, 0),
            "total_unrealized_pnl": round(total_eval - total_cost, 0),
        },
    }


@router.get("/paper/pnl")
def paper_pnl():
    c = _conn()
    d = datetime.now().strftime("%Y-%m-%d")
    daily = c.execute(
        "SELECT COALESCE(SUM(pnl),0) s FROM kis_paper_realized WHERE ts LIKE ?",
        (f"{d}%",),
    ).fetchone()[0]
    total = c.execute("SELECT COALESCE(SUM(pnl),0) s FROM kis_paper_realized").fetchone()[0]
    c.close()
    return {"daily_realized_pnl": round(float(daily or 0), 0), "total_realized_pnl": round(float(total or 0), 0)}


@router.post('/live/order')
def live_order_blocked():
    # 실전주문은 명시적 개발/승인 전까지 강제 차단
    raise HTTPException(status_code=403, detail='LIVE 주문은 현재 차단 상태입니다. 승인된 절차로만 활성화하세요.')
