"""
routes/buy_candidates.py — 매수후보 + 공매도 잔고 API

  GET    /api/buy-candidates
  POST   /api/buy-candidates
  PATCH  /api/buy-candidates/{stock_code}
  DELETE /api/buy-candidates/{stock_code}
  GET    /api/short-sell/{stock_code}
"""

import logging
import sqlite3 as _sl
from datetime import date as _date

import yfinance as _yf
from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter()

DB_PATH = "/Applications/stock_dashboard/stock.db"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS buy_candidates (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code   TEXT NOT NULL UNIQUE,
    stock_name   TEXT NOT NULL,
    mktcap       REAL,
    target_price REAL,
    ref_date1    TEXT, ref_price1 REAL,
    ref_date2    TEXT, ref_price2 REAL,
    memo         TEXT DEFAULT '',
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""


def _db():
    return _sl.connect(DB_PATH)


def _yf_close(code: str) -> float | None:
    """Yahoo Finance 최신 종가 (장외 시간에도 동작)."""
    try:
        hist = _yf.Ticker(f"{code}.KS").history(period="5d")
        if hist is None or hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception:
        return None


def _trade_signal(conn, code: str, curr: float) -> tuple[str, str]:
    """간단한 MA/RSI/MACD 기반 매매 신호 계산."""
    try:
        rows = conn.execute(
            "SELECT close, volume, inst_net_buy, frn_net_buy FROM price_history "
            "WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 270", (code,)
        ).fetchall()
        if len(rows) < 30:
            return "hold", ""

        closes = [r[0] for r in rows]
        vols   = [r[1] or 0 for r in rows]
        inst5  = sum(r[2] or 0 for r in rows[:5])
        frn5   = sum(r[3] or 0 for r in rows[:5])
        c      = closes[0]

        def _ma(n): return sum(closes[:n]) / n if len(closes) >= n else closes[0]
        ma5, ma20, ma60 = _ma(5), _ma(20), _ma(60)
        ma200  = _ma(200)
        high52 = max(closes[:252] if len(closes) >= 252 else closes)
        from_h = (c - high52) / high52 * 100
        avg_vol = sum(vols[1:21]) / 20 if len(vols) >= 21 else 1

        align_up   = c > ma5 > ma20 > ma60
        align_down = c < ma5 < ma20 < ma60
        supply_pos = inst5 > 0 and frn5 > 0
        vol_surge  = vols[0] > avg_vol * 1.8

        # RSI (14)
        g = []; l = []
        for i in range(1, min(15, len(closes))):
            d = closes[i - 1] - closes[i]
            (g if d > 0 else l).append(abs(d))
            (l if d > 0 else g).append(0)
        ag = sum(g) / len(g) if g else 0
        al = sum(l) / len(l) if l else 1
        rsi = round(100 - 100 / (1 + ag / al)) if al else 50

        # MACD
        def _ema(data, n):
            k = 2 / (n + 1); e = data[0]
            for v in data[1:]: e = v * k + e * (1 - k)
            return e
        ca = list(reversed(closes))
        macd_v = (_ema(ca[-12:], 12) - _ema(ca[-26:], 26)) if len(ca) >= 26 else 0

        if align_down and c < ma200:
            return "strong_sell", f"역배열+MA200아래 [RSI:{rsi}]"
        if c < ma20 and rsi < 45:
            return "sell", f"MA20이탈+RSI{rsi} [RSI:{rsi}]"
        if align_up and c > ma200 and 50 <= rsi <= 70 and supply_pos and vol_surge:
            return "strong_buy", f"완전정배열+RSI{rsi}+수급+거래급증 [RSI:{rsi}]"
        if align_up and from_h >= -10 and supply_pos and c > ma200:
            return "strong_buy", f"신고가근접({from_h:.0f}%)+정배열 [RSI:{rsi}]"
        if align_up and rsi >= 50 and macd_v > 0:
            return "buy", f"정배열+RSI{rsi}+MACD양전환 [RSI:{rsi}]"
        if c > ma20 > ma60 and supply_pos:
            return "buy", f"MA정배열+수급양호 [RSI:{rsi}]"
        if c > ma20:
            return "hold", f"MA20위 관망 [RSI:{rsi}]"
        return "caution", f"MA20아래 [RSI:{rsi}]"
    except Exception:
        return "hold", ""


# ── GET /api/buy-candidates ─────────────────────────────────────
@router.get("")
def get_buy_candidates():
    today = _date.today().isoformat()
    conn  = _db()
    conn.execute(_CREATE_TABLE); conn.commit()

    rows = conn.execute(
        "SELECT id,stock_code,stock_name,mktcap,target_price,"
        "ref_date1,ref_price1,ref_date2,ref_price2,memo "
        "FROM buy_candidates ORDER BY created_at DESC"
    ).fetchall()

    result = []
    for r in rows:
        code = r[1]

        # 현재가: price_history 오늘치 없으면 Yahoo보충
        ph = conn.execute(
            "SELECT close FROM price_history WHERE stock_code=? AND date>=? AND close>0 ORDER BY date DESC LIMIT 1",
            (code, today)
        ).fetchone()
        if not ph:
            yf_price = _yf_close(code)
            if yf_price and yf_price > 0:
                try:
                    conn.execute(
                        "INSERT OR REPLACE INTO price_history (stock_code,date,open,high,low,close,volume) VALUES (?,?,?,?,?,?,0)",
                        (code, today, yf_price, yf_price, yf_price, yf_price)
                    )
                    conn.commit()
                except Exception:
                    pass

        ph2  = conn.execute(
            "SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 2",
            (code,)
        ).fetchall()
        curr = ph2[0][0] if ph2 else 0
        prev = ph2[1][0] if len(ph2) > 1 else curr
        chg  = round((curr - prev) / prev * 100, 2) if prev else 0

        mkt_row = conn.execute(
            "SELECT market_cap FROM stock_universe WHERE stock_code=? AND market_cap>0 ORDER BY base_date DESC LIMIT 1",
            (code,)
        ).fetchone()
        mktcap = mkt_row[0] if mkt_row else r[3]

        def _ref_chg(ref_d, ref_p):
            return round((curr - ref_p) / ref_p * 100, 2) if ref_d and ref_p and curr else None

        sig, reason = _trade_signal(conn, code, curr)
        result.append({
            "id": r[0], "stock_code": code, "stock_name": r[2],
            "mktcap": mktcap, "current_price": curr, "change_pct": chg,
            "target_price": r[4],
            "ref_date1": r[5], "ref_price1": r[6], "ref_chg1": _ref_chg(r[5], r[6]),
            "ref_date2": r[7], "ref_price2": r[8], "ref_chg2": _ref_chg(r[7], r[8]),
            "memo": r[9] or "", "trade_signal": sig, "trade_reason": reason,
        })
    conn.close()
    return result


# ── POST /api/buy-candidates ────────────────────────────────────
@router.post("")
def add_buy_candidate(payload: dict):
    conn = _db()
    conn.execute(_CREATE_TABLE); conn.commit()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO buy_candidates "
            "(stock_code,stock_name,mktcap,target_price,ref_date1,ref_price1,ref_date2,ref_price2,memo,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
            (payload.get("stock_code"), payload.get("stock_name"), payload.get("mktcap"),
             payload.get("target_price"), payload.get("ref_date1"), payload.get("ref_price1"),
             payload.get("ref_date2"), payload.get("ref_price2"), payload.get("memo", ""))
        )
        conn.commit()
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


# ── PATCH /api/buy-candidates/{stock_code} ──────────────────────
@router.patch("/{stock_code}")
def update_buy_candidate(stock_code: str, payload: dict):
    fields = [k for k in ["target_price","ref_date1","ref_price1","ref_date2","ref_price2","memo"] if k in payload]
    if not fields:
        return {"status": "no change"}
    vals = [payload[k] for k in fields] + [stock_code]
    sets = ", ".join(f"{k}=?" for k in fields) + ", updated_at=CURRENT_TIMESTAMP"
    conn = _db()
    conn.execute(f"UPDATE buy_candidates SET {sets} WHERE stock_code=?", vals)
    conn.commit(); conn.close()
    return {"status": "ok"}


# ── DELETE /api/buy-candidates/{stock_code} ─────────────────────
@router.delete("/{stock_code}")
def delete_buy_candidate(stock_code: str):
    conn = _db()
    conn.execute("DELETE FROM buy_candidates WHERE stock_code=?", (stock_code,))
    conn.commit(); conn.close()
    return {"status": "ok"}


# ── GET /api/short-sell/{stock_code} ───────────────────────────
@router.get("/short-sell/{stock_code}")
def get_short_sell(stock_code: str):
    conn = _db()
    rows = conn.execute(
        "SELECT bas_dt, borrow_bal_qty FROM short_sell_daily "
        "WHERE stock_code=? AND borrow_bal_qty IS NOT NULL "
        "ORDER BY bas_dt DESC LIMIT 60",
        (stock_code,)
    ).fetchall()
    conn.close()
    if len(rows) < 5:
        return None

    def _avg(rs, s, e):
        vals = [r[1] for r in rs[s:e] if r[1]]
        return sum(vals) / len(vals) if vals else 0

    today_val = rows[0][1] or 0
    avg5      = _avg(rows, 0, 5)
    avg5_prev = _avg(rows, 5, 10)
    return {
        "today": today_val, "avg5": avg5, "avg5_prev": avg5_prev,
        "today_signal": "green" if today_val < avg5      else "red",
        "week_signal":  "green" if avg5      < avg5_prev else "red",
        "latest_date":  rows[0][0] if rows else None,
    }
