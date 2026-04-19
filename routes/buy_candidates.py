"""
routes/buy_candidates.py — 매수후보 + 공매도 잔고 API

  GET    /api/buy-candidates
  POST   /api/buy-candidates
  PATCH  /api/buy-candidates/{stock_code}
  DELETE /api/buy-candidates/{stock_code}
  GET    /api/buy-candidates/short-sell/{stock_code}
"""

import logging
import sqlite3 as _sl
from datetime import date as _date

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
    conn = _sl.connect(DB_PATH)
    conn.row_factory = _sl.Row
    return conn


def _trade_signal_from_rows(rows: list, curr: float) -> tuple[str, str]:
    """MA/RSI/MACD 기반 매매 신호 — 미리 가져온 rows에서 계산."""
    try:
        if len(rows) < 30:
            return "hold", ""

        closes = [r[0] for r in rows]
        vols   = [r[1] or 0 for r in rows]
        inst5  = sum(r[2] or 0 for r in rows[:5])
        frn5   = sum(r[3] or 0 for r in rows[:5])

        def _ma(n): return sum(closes[:n]) / n if len(closes) >= n else closes[0]
        ma5, ma20, ma60 = _ma(5), _ma(20), _ma(60)
        ma200  = _ma(200)
        high52 = max(closes[:252] if len(closes) >= 252 else closes)
        from_h = (curr - high52) / high52 * 100
        avg_vol = sum(vols[1:21]) / 20 if len(vols) >= 21 else 1

        align_up   = curr > ma5 > ma20 > ma60
        align_down = curr < ma5 < ma20 < ma60
        supply_pos = inst5 > 0 and frn5 > 0
        vol_surge  = vols[0] > avg_vol * 1.8

        # RSI (14)
        g, l = [], []
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

        if align_down and curr < ma200:
            return "strong_sell", f"역배열+MA200아래 [RSI:{rsi}]"
        if curr < ma20 and rsi < 45:
            return "sell", f"MA20이탈+RSI{rsi} [RSI:{rsi}]"
        if align_up and curr > ma200 and 50 <= rsi <= 70 and supply_pos and vol_surge:
            return "strong_buy", f"완전정배열+RSI{rsi}+수급+거래급증 [RSI:{rsi}]"
        if align_up and from_h >= -10 and supply_pos and curr > ma200:
            return "strong_buy", f"신고가근접({from_h:.0f}%)+정배열 [RSI:{rsi}]"
        if align_up and rsi >= 50 and macd_v > 0:
            return "buy", f"정배열+RSI{rsi}+MACD양전환 [RSI:{rsi}]"
        if curr > ma20 > ma60 and supply_pos:
            return "buy", f"MA정배열+수급양호 [RSI:{rsi}]"
        if curr > ma20:
            return "hold", f"MA20위 관망 [RSI:{rsi}]"
        return "caution", f"MA20아래 [RSI:{rsi}]"
    except Exception:
        return "hold", ""


# ── GET /api/buy-candidates ─────────────────────────────────────
@router.get("")
def get_buy_candidates():
    conn = _db()
    conn.execute(_CREATE_TABLE)
    conn.commit()

    rows = conn.execute(
        "SELECT id,stock_code,stock_name,mktcap,target_price,"
        "ref_date1,ref_price1,ref_date2,ref_price2,memo "
        "FROM buy_candidates ORDER BY created_at DESC"
    ).fetchall()

    if not rows:
        conn.close()
        return []

    codes = [r["stock_code"] for r in rows]
    ph = "," .join("?" * len(codes))

    # ── 배치: 최근 2일 종가 ──────────────────────────────────
    price_raw = conn.execute(
        f"""SELECT stock_code, close FROM price_history
            WHERE stock_code IN ({ph}) AND close > 0
            ORDER BY stock_code, date DESC""",
        codes,
    ).fetchall()
    from collections import defaultdict
    price_map: dict[str, list[float]] = defaultdict(list)
    for pr in price_raw:
        if len(price_map[pr[0]]) < 2:
            price_map[pr[0]].append(pr[1])

    # ── 배치: 시가총액 ───────────────────────────────────────
    mktcap_raw = conn.execute(
        f"""SELECT stock_code, MAX(market_cap) FROM stock_universe
            WHERE stock_code IN ({ph}) AND market_cap > 0
            GROUP BY stock_code""",
        codes,
    ).fetchall()
    mktcap_map = {r[0]: r[1] for r in mktcap_raw}

    # ── 배치: 시그널 계산용 가격/수급 (전종목 한번에) ───────
    hist_raw = conn.execute(
        f"""SELECT stock_code, close, volume, inst_net_buy, frn_net_buy
            FROM price_history
            WHERE stock_code IN ({ph}) AND close > 0
            ORDER BY stock_code, date DESC""",
        codes,
    ).fetchall()
    hist_map: dict[str, list] = defaultdict(list)
    for hr in hist_raw:
        if len(hist_map[hr[0]]) < 270:
            hist_map[hr[0]].append((hr[1], hr[2], hr[3], hr[4]))

    result = []
    for r in rows:
        code  = r["stock_code"]
        p     = price_map.get(code, [])
        curr  = p[0] if p else 0.0
        prev  = p[1] if len(p) > 1 else curr
        chg   = round((curr - prev) / prev * 100, 2) if prev else 0.0
        mktcap = mktcap_map.get(code, r["mktcap"])

        def _ref_chg(ref_p):
            return round((curr - ref_p) / ref_p * 100, 2) if ref_p and curr else None

        sig, reason = _trade_signal_from_rows(hist_map.get(code, []), curr)
        result.append({
            "id": r["id"], "stock_code": code, "stock_name": r["stock_name"],
            "mktcap": mktcap, "current_price": curr, "change_pct": chg,
            "target_price": r["target_price"],
            "ref_date1": r["ref_date1"], "ref_price1": r["ref_price1"],
            "ref_chg1": _ref_chg(r["ref_price1"]),
            "ref_date2": r["ref_date2"], "ref_price2": r["ref_price2"],
            "ref_chg2": _ref_chg(r["ref_price2"]),
            "memo": r["memo"] or "", "trade_signal": sig, "trade_reason": reason,
        })
    conn.close()
    return result


# ── POST /api/buy-candidates ────────────────────────────────────
@router.post("")
def add_buy_candidate(payload: dict):
    conn = _db()
    conn.execute(_CREATE_TABLE)
    conn.commit()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO buy_candidates "
            "(stock_code,stock_name,mktcap,target_price,"
            "ref_date1,ref_price1,ref_date2,ref_price2,memo,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
            (payload.get("stock_code"), payload.get("stock_name"), payload.get("mktcap"),
             payload.get("target_price"), payload.get("ref_date1"), payload.get("ref_price1"),
             payload.get("ref_date2"), payload.get("ref_price2"), payload.get("memo", "")),
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
    conn.commit()
    conn.close()
    return {"status": "ok"}


# ── DELETE /api/buy-candidates/{stock_code} ─────────────────────
@router.delete("/{stock_code}")
def delete_buy_candidate(stock_code: str):
    conn = _db()
    conn.execute("DELETE FROM buy_candidates WHERE stock_code=?", (stock_code,))
    conn.commit()
    conn.close()
    return {"status": "ok"}


# ── GET /api/buy-candidates/short-sell/{stock_code} ────────────
@router.get("/short-sell/{stock_code}")
def get_short_sell(stock_code: str):
    conn = _db()
    rows = conn.execute(
        "SELECT bas_dt, borrow_bal_qty FROM short_sell_daily "
        "WHERE stock_code=? AND borrow_bal_qty IS NOT NULL "
        "ORDER BY bas_dt DESC LIMIT 60",
        (stock_code,),
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
