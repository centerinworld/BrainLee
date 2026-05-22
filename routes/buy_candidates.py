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

_CREATE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_ph_code_date_close ON price_history(stock_code, date DESC, close);
CREATE INDEX IF NOT EXISTS idx_su_code_base ON stock_universe(stock_code, base_date DESC, market_cap);
"""


def _db():
    return _sl.connect(DB_PATH)


# ── GET /api/buy-candidates ─────────────────────────────────────
@router.get("")
def get_buy_candidates():
    conn  = _db()
    conn.execute(_CREATE_TABLE)
    conn.executescript(_CREATE_INDEXES)
    conn.commit()

    rows = conn.execute(
        "SELECT id,stock_code,stock_name,mktcap,target_price,"
        "ref_date1,ref_price1,ref_date2,ref_price2,memo "
        "FROM buy_candidates ORDER BY created_at DESC"
    ).fetchall()

    if not rows:
        conn.close()
        return []

    codes = [r[1] for r in rows if r[1]]
    ph = ",".join("?" for _ in codes)

    # 현재가/직전가 배치 조회 (휴장일에도 최신 거래일 기준으로 계산 가능)
    price_map = {}
    if codes:
        q_price = f"""
            WITH ranked AS (
                SELECT stock_code, close,
                       ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY date DESC) AS rn
                FROM price_history
                WHERE stock_code IN ({ph}) AND close > 0
            )
            SELECT stock_code, close, rn
            FROM ranked
            WHERE rn <= 2
            ORDER BY stock_code, rn
        """
        for sc, close, rn in conn.execute(q_price, codes).fetchall():
            slot = price_map.setdefault(sc, {"curr": 0.0, "prev": 0.0})
            if rn == 1:
                slot["curr"] = float(close or 0.0)
            elif rn == 2:
                slot["prev"] = float(close or 0.0)
        for sc, p in price_map.items():
            if not p["prev"]:
                p["prev"] = p["curr"]

    # 시총 배치 조회
    mktcap_map = {}
    if codes:
        q_mkt = f"""
            WITH ranked AS (
                SELECT stock_code, market_cap,
                       ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY base_date DESC) AS rn
                FROM stock_universe
                WHERE stock_code IN ({ph}) AND market_cap > 0
            )
            SELECT stock_code, market_cap FROM ranked WHERE rn=1
        """
        for sc, mc in conn.execute(q_mkt, codes).fetchall():
            mktcap_map[sc] = mc

    # 신호 계산용 가격/수급 히스토리 배치 조회 (종목별 최대 270행)
    hist_map = {}
    if codes:
        q_hist = f"""
            WITH ranked AS (
                SELECT stock_code, close, volume, inst_net_buy, frn_net_buy,
                       ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY date DESC) AS rn
                FROM price_history
                WHERE stock_code IN ({ph}) AND close > 0
            )
            SELECT stock_code, close, volume, inst_net_buy, frn_net_buy
            FROM ranked
            WHERE rn <= 270
            ORDER BY stock_code, rn
        """
        for row in conn.execute(q_hist, codes).fetchall():
            sc = row[0]
            hist_map.setdefault(sc, []).append((row[1], row[2], row[3], row[4]))

    def _trade_signal_from_rows(rows270: list[tuple]) -> tuple[str, str]:
        try:
            if len(rows270) < 30:
                return "hold", ""
            closes = [r[0] for r in rows270]
            vols   = [r[1] or 0 for r in rows270]
            inst5  = sum(r[2] or 0 for r in rows270[:5])
            frn5   = sum(r[3] or 0 for r in rows270[:5])
            c      = closes[0]

            def _ma(n): return sum(closes[:n]) / n if len(closes) >= n else closes[0]
            ma5, ma20, ma60 = _ma(5), _ma(20), _ma(60)
            ma200 = _ma(200)
            high52 = max(closes[:252] if len(closes) >= 252 else closes)
            from_h = (c - high52) / high52 * 100
            avg_vol = sum(vols[1:21]) / 20 if len(vols) >= 21 else 1

            align_up = c > ma5 > ma20 > ma60
            align_down = c < ma5 < ma20 < ma60
            supply_pos = inst5 > 0 and frn5 > 0
            vol_surge = vols[0] > avg_vol * 1.8

            g = []; l = []
            for i in range(1, min(15, len(closes))):
                d = closes[i - 1] - closes[i]
                (g if d > 0 else l).append(abs(d))
                (l if d > 0 else g).append(0)
            ag = sum(g) / len(g) if g else 0
            al = sum(l) / len(l) if l else 1
            rsi = round(100 - 100 / (1 + ag / al)) if al else 50

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

    result = []
    for r in rows:
        code = r[1]
        p = price_map.get(code, {"curr": 0.0, "prev": 0.0})
        curr = p["curr"]
        prev = p["prev"] if p["prev"] else curr
        chg  = round((curr - prev) / prev * 100, 2) if prev else 0

        mktcap = mktcap_map.get(code, r[3])

        def _ref_chg(ref_d, ref_p):
            return round((curr - ref_p) / ref_p * 100, 2) if ref_d and ref_p and curr else None

        sig, reason = _trade_signal_from_rows(hist_map.get(code, []))
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
