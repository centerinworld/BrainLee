"""
routes/trend.py — 스탁이지 가상매매 + AI 자동매매 API

  GET  /api/trend/holdings
  POST /api/trend/buy
  POST /api/trend/update
  POST /api/trend/sell
  GET  /api/trend/trades
  GET  /api/trend/summary
  GET  /api/trend/ai-holdings
  POST /api/trend/ai-combo/execute
  DELETE /api/trend/trades/all
"""

import sqlite3 as _sl
import logging
from datetime import date as _date, datetime as _dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()

DB_PATH = "stock.db"


def _db():
    return _sl.connect(DB_PATH)


# ── GET /api/trend/holdings ─────────────────────────────────────
@router.get("/holdings")
def get_trend_holdings(db: Session = Depends(get_db)):
    conn   = _db()
    rows   = conn.execute("""
        SELECT id, stock_name, buy_price, current_price,
               quantity, profit_pct, sell_price, sold_at, is_active,
               hold_days, sector, updated_at, entry_date, sold_price, strategy, stock_code
        FROM peak_holding ORDER BY entry_date DESC
    """).fetchall()
    result = []

    for r in rows:
        stock_name = r[1]
        buy_price  = r[2] or 0
        quantity   = r[4] or 0
        is_active  = bool(r[8])
        stock_code = r[15] or ""

        # stock_code 없으면 universe에서 조회 후 캐시
        if not stock_code:
            for tbl in ("stock_universe", "listed_company_info"):
                row = conn.execute(
                    f"SELECT stock_code FROM {tbl} WHERE stock_name=? LIMIT 1", (stock_name,)
                ).fetchone()
                if row:
                    stock_code = row[0]
                    conn.execute("UPDATE peak_holding SET stock_code=? WHERE id=?", (stock_code, r[0]))
                    conn.commit()
                    break

        # 현재가: KIS가 1분마다 price_history에 기록 → 가장 최근 close 사용
        # 장중: 오늘 KIS 수집 데이터, 장 종료 후: 마지막 저장된 종가 자동 반환
        if is_active and stock_code:
            row = conn.execute(
                "SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 1",
                (stock_code,)
            ).fetchone()
            current_price = row[0] if row else (r[3] or buy_price)
        else:
            current_price = r[3] or buy_price

        profit      = round((current_price - buy_price) * quantity) if buy_price and quantity else 0
        profit_pct  = round((current_price - buy_price) / buy_price * 100, 2) if buy_price else 0
        total_value = round(current_price * quantity) if current_price and quantity else 0

        result.append({
            "id": r[0], "stock_code": stock_code, "stock_name": stock_name,
            "buy_price": buy_price, "current_price": current_price, "quantity": quantity,
            "profit_pct": profit_pct, "profit": profit, "total_value": total_value,
            "sell_price": r[6], "sold_at": r[7],
            "is_active": is_active, "hold_days": r[9],
            "sector": r[10], "updated_at": r[11],
            "entry_date": r[12], "strategy": r[14],
        })

    conn.close()
    return result


# ── POST /api/trend/buy ─────────────────────────────────────────
@router.post("/buy")
def trend_buy(payload: dict):
    stock_name = payload.get("stock_name", "")
    stock_code = payload.get("stock_code", "")
    buy_price  = float(payload.get("current_price") or payload.get("buy_price") or 0)
    quantity   = int(payload.get("quantity") or 0)
    entry_date = payload.get("entry_date") or _dt.now().strftime("%Y-%m-%d")
    strategy   = payload.get("strategy", "peak")
    sector     = payload.get("sector", "")

    if not stock_name or not buy_price:
        raise HTTPException(status_code=400, detail="stock_name, buy_price 필수")

    conn = _db()
    if not stock_code:
        row = conn.execute(
            "SELECT stock_code FROM stock_universe WHERE stock_name=? LIMIT 1", (stock_name,)
        ).fetchone()
        if row:
            stock_code = row[0]

    # 중복 방지
    dup = conn.execute(
        "SELECT id FROM peak_holding WHERE stock_name=? AND entry_date=? AND strategy=?",
        (stock_name, entry_date, strategy)
    ).fetchone()
    if dup:
        conn.execute(
            "UPDATE peak_holding SET is_active=1, current_price=?, stock_code=COALESCE(stock_code,?), updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (buy_price, stock_code or None, dup[0])
        )
        conn.commit(); conn.close()
        return {"status": "ok", "stock_name": stock_name}

    active = conn.execute(
        "SELECT id FROM peak_holding WHERE stock_name=? AND is_active=1 AND strategy=?",
        (stock_name, strategy)
    ).fetchone()
    if not active:
        conn.execute(
            "INSERT INTO peak_holding (stock_code,stock_name,sector,buy_price,current_price,quantity,"
            "entry_date,hold_days,profit_pct,is_active,strategy,detected_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,0,0.0,1,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
            (stock_code or None, stock_name, sector, buy_price, buy_price, quantity, entry_date, strategy)
        )
        conn.execute(
            "INSERT INTO peak_trade (stock_name,tx_type,price,quantity,total_amount,profit,profit_pct,tx_at,strategy) "
            "VALUES (?,?,?,?,?,0,0.0,CURRENT_TIMESTAMP,?)",
            (stock_name, "buy", buy_price, quantity, round(buy_price * quantity), strategy)
        )
    conn.commit(); conn.close()
    return {"status": "ok", "stock_name": stock_name}


# ── POST /api/trend/update ──────────────────────────────────────
@router.post("/update")
def trend_update(payload: dict):
    stock_name    = payload.get("stock_name", "")
    strategy      = payload.get("strategy", "peak")
    current_price = float(payload.get("current_price") or 0)
    hold_days     = int(payload.get("hold_days") or 0)
    profit_pct    = float(payload.get("profit_pct") or 0)

    if not stock_name:
        return {"status": "skip"}

    conn = _db()
    conn.execute(
        "UPDATE peak_holding SET current_price=?, hold_days=?, profit_pct=?, updated_at=CURRENT_TIMESTAMP "
        "WHERE stock_name=? AND strategy=? AND is_active=1",
        (current_price, hold_days, profit_pct, stock_name, strategy)
    )
    conn.commit(); conn.close()
    return {"status": "ok"}


# ── POST /api/trend/sell ────────────────────────────────────────
@router.post("/sell")
def trend_sell(payload: dict):
    stock_name = payload.get("stock_name", "")
    strategy   = payload.get("strategy", "peak")
    sell_price = float(payload.get("sell_price") or 0)
    profit     = float(payload.get("profit") or 0)
    profit_pct = float(payload.get("profit_pct") or 0)
    sold_at    = payload.get("sold_at") or _dt.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = _db()
    conn.execute(
        "UPDATE peak_holding SET is_active=0, sell_price=?, sold_at=?, current_price=?, profit_pct=?, updated_at=CURRENT_TIMESTAMP "
        "WHERE stock_name=? AND strategy=? AND is_active=1",
        (sell_price, sold_at, sell_price, profit_pct, stock_name, strategy)
    )
    row = conn.execute(
        "SELECT id, quantity, strategy FROM peak_holding WHERE stock_name=? AND strategy=? ORDER BY id DESC LIMIT 1",
        (stock_name, strategy)
    ).fetchone()
    if row:
        qty = row[1] or 0
        conn.execute(
            "INSERT INTO peak_trade (stock_name,tx_type,price,quantity,total_amount,profit,profit_pct,tx_at,strategy) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (stock_name, "sell", sell_price, qty, round(sell_price * qty), profit, profit_pct, sold_at, row[2] or "peak")
        )
    conn.commit(); conn.close()
    return {"status": "ok", "stock_name": stock_name}


# ── GET /api/trend/trades ───────────────────────────────────────
@router.get("/trades")
def get_trend_trades(db: Session = Depends(get_db)):
    conn = _db()
    rows = conn.execute(
        "SELECT id,'' as stock_code,stock_name,tx_type,price,quantity,total_amount,profit,profit_pct,tx_at,strategy "
        "FROM peak_trade ORDER BY tx_at DESC LIMIT 100"
    ).fetchall()
    conn.close()
    keys = ["id","stock_code","stock_name","tx_type","price","quantity","total_amount","profit","profit_pct","tx_at","strategy"]
    return [dict(zip(keys, r)) for r in rows]


# ── GET /api/trend/summary ──────────────────────────────────────
@router.get("/summary")
def get_trend_summary(db: Session = Depends(get_db)):
    conn = _db()
    row  = conn.execute(
        "SELECT COUNT(*), SUM(CASE WHEN profit_pct>0 THEN 1 ELSE 0 END), "
        "SUM((sell_price-buy_price)*quantity) FROM peak_holding WHERE is_active=0"
    ).fetchone()
    conn.close()
    total = row[0] or 0
    wins  = row[1] or 0
    return {
        "total_trades": total,
        "win_count":    wins,
        "win_rate":     round(wins / total * 100, 1) if total else None,
        "total_profit": round(row[2] or 0),
    }


# ── GET /api/trend/ai-holdings ──────────────────────────────────
@router.get("/ai-holdings")
def get_ai_holdings():
    conn = _db()
    rows = conn.execute(
        "SELECT stock_name,sector,buy_price,current_price,quantity,entry_date,hold_days,profit_pct,"
        "       detected_at,is_active,id,sell_price,sold_at,stock_code "
        "FROM peak_holding WHERE strategy='ai_combo' "
        "ORDER BY is_active DESC, entry_date DESC, id DESC LIMIT 50"
    ).fetchall()
    result = []
    for r in rows:
        stock_code  = r[13]
        buy_price   = r[2] or 0
        quantity    = r[4] or 0
        is_active   = bool(r[9])
        sell_price  = r[11]

        if is_active and stock_code:
            pr = conn.execute(
                "SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 1",
                (stock_code,)
            ).fetchone()
            current_price = pr[0] if pr else (r[3] or buy_price)
        else:
            current_price = sell_price or r[3] or buy_price

        profit_pct  = round((current_price - buy_price) / buy_price * 100, 2) if buy_price else 0
        result.append({
            "stock_code": stock_code, "stock_name": r[0], "sector": r[1],
            "buy_price": buy_price, "current_price": current_price, "quantity": quantity,
            "entry_date": r[5], "hold_days": r[6],
            "profit_pct": profit_pct,
            "profit": round((current_price - buy_price) * quantity),
            "total_value": round(current_price * quantity),
            "detected_at": r[8], "is_active": is_active, "id": r[10],
            "sell_price": sell_price, "sold_at": r[12],
        })
    conn.close()
    return result


# ── POST /api/trend/ai-combo/execute ────────────────────────────
@router.post("/ai-combo/execute")
def execute_ai_combo_now():
    """현재 combo-candidates 기준으로 AI 자동매매 즉시 실행."""
    # _signal_cache와 _process_ai_combo_autotrade는 main에서 임포트 (지연)
    import main as _main
    combo = _main._signal_cache.get("combo_candidates", {}).get("data", [])
    if not combo:
        import threading
        threading.Thread(target=_main._run_screener_precompute, daemon=True).start()
        return {"status": "computing", "message": "스크리너 계산 중... 30초 후 다시 시도하세요."}
    _main._process_ai_combo_autotrade(combo)
    conn = _db()
    cnt = conn.execute(
        "SELECT COUNT(*) FROM peak_holding WHERE strategy='ai_combo' AND is_active=1"
    ).fetchone()[0]
    conn.close()
    return {"status": "ok", "message": f"AI 자동매매 실행 완료. 현재 보유: {cnt}종목", "active": cnt}


# ── DELETE /api/trend/trades/all ────────────────────────────────
@router.delete("/trades/all")
def clear_all_trades():
    conn = _db()
    conn.execute("DELETE FROM peak_trade")
    conn.commit(); conn.close()
    return {"status": "ok", "message": "매매 내역이 모두 삭제되었습니다."}
