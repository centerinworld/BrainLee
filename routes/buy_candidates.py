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
import threading
import time
from datetime import datetime
from fastapi import APIRouter, HTTPException

from routes.cherry_screener import _cache_read as _cherry_cache_read
from routes.cherry_screener import refresh_cherry_screener_cache
from routes.company_intelligence import _compute_company_intelligence
from routes.trend import (
    get_cm_recommendations,
    get_gc_recommendations,
    get_rec_recommendations,
    get_turnover_recommendations,
    get_v18_recommendations,
)
from services.short_sale_service import get_actual_short_sale_rows

logger = logging.getLogger(__name__)
router = APIRouter()

DB_PATH = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"
_AUTO_BOARD_CACHE: dict = {"data": None, "at": 0.0, "computing": False}
_AUTO_BOARD_CACHE_TTL_SEC = 300

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
    conn = _sl.connect(DB_PATH, timeout=30)
    if isinstance(conn, _sl.Connection):
        conn.row_factory = _sl.Row
    return conn


def _safe_num(value):
    try:
        return float(value)
    except Exception:
        return None


def _summarize_source_labels(source_keys: list[str]) -> list[str]:
    labels = {
        "manual": "수동 후보",
        "cherry_3": "체리 3대스크린",
        "cherry_2": "체리 2대스크린",
        "gc": "전략센터 GC",
        "cm": "전략센터 계약모멘텀",
        "rec": "전략센터 리커버리",
        "v18": "전략센터 V18",
        "turnover": "전략센터 회전율",
    }
    ordered = []
    for key in source_keys:
        if key in labels and labels[key] not in ordered:
            ordered.append(labels[key])
    return ordered


def _parse_date_text(value):
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("/", "-").replace(".", "-")[:10]
    for fmt in ("%Y-%m-%d", "%y-%m-%d", "%Y-%m", "%y-%m"):
        try:
            parsed = datetime.strptime(text, fmt)
            if fmt in ("%Y-%m", "%y-%m"):
                return parsed.replace(day=1)
            return parsed
        except Exception:
            continue
    return None


def _latest_date_text(values):
    latest = None
    for value in values:
        parsed = _parse_date_text(value)
        if parsed and (latest is None or parsed > latest):
            latest = parsed
    return latest.strftime("%Y-%m-%d") if latest else None


def _peer_advantage_summary(intel: dict) -> str:
    peers = intel.get("peer_candidates") or []
    diffs = intel.get("differentiations") or []
    bulls = intel.get("bull_points") or []
    if peers and diffs:
        peer_names = ", ".join(p.get("stock_name", "") for p in peers[:2] if p.get("stock_name"))
        return f"{peer_names} 대비 {diffs[0]} 포인트가 먼저 눈에 띈다."
    if peers and bulls:
        peer_names = ", ".join(p.get("stock_name", "") for p in peers[:2] if p.get("stock_name"))
        return f"{peer_names} 대비 {bulls[0]} 논리가 상대적으로 선명하다."
    if diffs:
        return f"동종사 대비 {diffs[0]}가 차별화 포인트다."
    return "경쟁사 대비 차별화 포인트를 추가 학습 중이다."


def _build_auto_board() -> dict:
    conn = _db()
    conn.row_factory = _sl.Row
    try:
        manual_rows = conn.execute(
            "SELECT stock_code, stock_name, target_price, memo FROM buy_candidates ORDER BY created_at DESC"
        ).fetchall()
        manual_map = {
            str(r["stock_code"]): {
                "stock_name": r["stock_name"],
                "target_price": r["target_price"],
                "memo": r["memo"] or "",
            }
            for r in manual_rows
            if r["stock_code"]
        }

        cherry = _cherry_cache_read() or refresh_cherry_screener_cache()
        cherry3 = cherry.get("three_screen_pass", [])[:80]
        cherry2 = cherry.get("two_screen_pass", [])[:120]
        gc = (get_gc_recommendations() or {}).get("buy_candidates", [])[:20]
        cm = (get_cm_recommendations() or {}).get("buy_candidates", [])[:20]
        rec = (get_rec_recommendations() or {}).get("buy_candidates", [])[:20]
        v18 = (get_v18_recommendations() or {}).get("buy_candidates", [])[:20]
        turnover = (get_turnover_recommendations() or {}).get("candidates", [])[:20]

        pool: dict[str, dict] = {}

        def add_candidate(code: str, name: str, source_key: str, weight: int, extra: dict | None = None):
            if not code:
                return
            item = pool.setdefault(code, {
                "stock_code": code,
                "stock_name": name or code,
                "score": 0,
                "sources": [],
                "source_labels": [],
                "target_price": None,
                "strategy_reasons": [],
                "cherry_score": None,
                "cherry_flags": {},
            })
            item["score"] += weight
            if source_key not in item["sources"]:
                item["sources"].append(source_key)
            if extra:
                if extra.get("target_price") and item["target_price"] is None:
                    item["target_price"] = extra["target_price"]
                if extra.get("reason"):
                    item["strategy_reasons"].append(extra["reason"])
                if extra.get("cherry_score") is not None:
                    item["cherry_score"] = extra["cherry_score"]
                if extra.get("cherry_flags"):
                    item["cherry_flags"].update(extra["cherry_flags"])

        for code, row in manual_map.items():
            add_candidate(code, row["stock_name"], "manual", 2, {"target_price": row.get("target_price"), "reason": row.get("memo")})
        for row in cherry3:
            add_candidate(
                str(row.get("stock_code") or ""),
                row.get("stock_name") or "",
                "cherry_3",
                5,
                {
                    "cherry_score": row.get("score"),
                    "cherry_flags": {
                        "turnaround": bool(row.get("screen1_turnaround")),
                        "revenue_ath": bool(row.get("screen2_revenue_ath")),
                        "undervalued": bool(row.get("screen3_undervalued_vs_sector")),
                    },
                },
            )
        for row in cherry2:
            add_candidate(
                str(row.get("stock_code") or ""),
                row.get("stock_name") or "",
                "cherry_2",
                3,
                {
                    "cherry_score": row.get("score"),
                    "cherry_flags": {
                        "turnaround": bool(row.get("screen1_turnaround")),
                        "revenue_ath": bool(row.get("screen2_revenue_ath")),
                        "undervalued": bool(row.get("screen3_undervalued_vs_sector")),
                    },
                },
            )
        for source_key, rows, weight in (
            ("gc", gc, 3),
            ("cm", cm, 4),
            ("rec", rec, 3),
            ("v18", v18, 4),
            ("turnover", turnover, 2),
        ):
            for row in rows:
                add_candidate(
                    str(row.get("stock_code") or ""),
                    row.get("stock_name") or "",
                    source_key,
                    weight,
                    {"reason": row.get("reason") or row.get("label") or row.get("summary")},
                )

        # 최종 화면은 40개만 사용하므로 전체 후보에 고비용 기업 분석을 하지 않는다.
        ranked_pool = sorted(
            pool.items(),
            key=lambda pair: (-pair[1]["score"], -len(pair[1]["sources"]), pair[0]),
        )[:50]
        items = []
        for code, item in ranked_pool:
            intel = _compute_company_intelligence(conn, code)
            if not intel.get("found"):
                continue
            analyst_view = intel.get("analyst_view") or {}
            current_price = _safe_num(intel.get("current_price"))
            avg_target = _safe_num(analyst_view.get("avg_target_price") or item.get("target_price"))
            target_gap = None
            if current_price and avg_target:
                target_gap = round((avg_target - current_price) / current_price * 100, 1)
            item["source_labels"] = _summarize_source_labels(item["sources"])
            item["cherry_analysis"] = {
                "value_chain_position": intel.get("value_chain_position"),
                "main_products": (intel.get("main_products") or [])[:3],
                "bull_points": (intel.get("bull_points") or [])[:3],
                "bear_points": (intel.get("bear_points") or [])[:2],
                "period_summary": intel.get("period_comparison_summary"),
            }
            item["analyst_analysis"] = {
                "coverage_count": analyst_view.get("consensus_count", 0),
                "extract_count": analyst_view.get("extract_count", 0),
                "avg_target_price": avg_target,
                "target_gap_pct": target_gap,
                "positive_themes": (analyst_view.get("positive_themes") or [])[:4],
                "risk_themes": (analyst_view.get("risk_themes") or [])[:3],
            }
            item["peer_view"] = {
                "peers": [
                    {"stock_code": p.get("stock_code"), "stock_name": p.get("stock_name")}
                    for p in (intel.get("peer_candidates") or [])[:3]
                ],
                "why_this_company": _peer_advantage_summary(intel),
            }
            item["why_buy_now"] = (intel.get("bull_points") or [None])[0] or (analyst_view.get("positive_themes") or [None])[0] or "후보 논리 보강 중"
            item["analysis_link_ready"] = True
            item["analysis_as_of"] = intel.get("analysis_as_of") or intel.get("price_date")
            items.append(item)

        items.sort(
            key=lambda row: (
                -row["score"],
                -len(row["sources"]),
                -((row["analyst_analysis"].get("coverage_count") or 0) + (row["analyst_analysis"].get("extract_count") or 0)),
                -(row["analyst_analysis"].get("target_gap_pct") if row["analyst_analysis"].get("target_gap_pct") is not None else -9999),
            )
        )
        return {
            "as_of": _latest_date_text([item.get("analysis_as_of") for item in items]),
            "summary": {
                "candidate_count": len(items),
                "from_cherry": len([x for x in items if any(s.startswith("cherry_") for s in x["sources"])]),
                "from_strategy_center": len([x for x in items if any(s in {"gc", "cm", "rec", "v18", "turnover"} for s in x["sources"])]),
                "with_analyst_coverage": len([x for x in items if (x["analyst_analysis"].get("coverage_count") or 0) > 0 or (x["analyst_analysis"].get("extract_count") or 0) > 0]),
            },
            "items": items[:40],
        }
    finally:
        conn.close()


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


@router.get("/auto-board")
def get_auto_buy_candidate_board():
    cached = _AUTO_BOARD_CACHE.get("data")
    if cached and time.time() - _AUTO_BOARD_CACHE["at"] < _AUTO_BOARD_CACHE_TTL_SEC:
        return cached

    if not _AUTO_BOARD_CACHE.get("computing"):
        _AUTO_BOARD_CACHE["computing"] = True

        def _refresh():
            try:
                _AUTO_BOARD_CACHE["data"] = _build_auto_board()
                _AUTO_BOARD_CACHE["at"] = time.time()
            except Exception as e:
                logger.exception("[매수후보 자동보드] %s", e)
            finally:
                _AUTO_BOARD_CACHE["computing"] = False

        threading.Thread(target=_refresh, daemon=True, name="AutoBoardRefresh").start()

    if cached:
        return cached
    return {
        "as_of": None,
        "summary": {
            "candidate_count": 0,
            "from_cherry": 0,
            "from_strategy_center": 0,
            "with_analyst_coverage": 0,
        },
        "items": [],
        "refreshing": True,
    }


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
    borrow_rows = conn.execute(
        """
        SELECT bas_dt, borrow_bal_qty, borrow_bal_amt, borrow_bal_pct
        FROM short_sell_daily
        WHERE stock_code=? AND borrow_bal_qty IS NOT NULL
        ORDER BY bas_dt DESC LIMIT 60
        """,
        (stock_code,)
    ).fetchall()
    conn.close()
    short_rows = get_actual_short_sale_rows(stock_code, limit=60)
    if not borrow_rows and not short_rows:
        return None

    def _avg(rs, column, start, end):
        vals = [float(r[column]) for r in rs[start:end] if r[column] is not None]
        return sum(vals) / len(vals) if vals else 0

    def _weighted_ratio(rs, value_col, total_col, start, end):
        selected = [r for r in rs[start:end] if r.get(value_col) is not None and r.get(total_col)]
        total = sum(float(r[total_col]) for r in selected)
        value = sum(float(r[value_col]) for r in selected)
        return value / total * 100 if total > 0 else None

    def _pressure_signal(current, baseline):
        if current is None or baseline is None or baseline <= 0:
            return "neutral"
        return "green" if current <= baseline else "red"

    borrow_latest = borrow_rows[0] if borrow_rows else None
    short_latest = short_rows[0] if short_rows else None
    today_val = float(borrow_latest["borrow_bal_qty"] or 0) if borrow_latest else None
    avg5 = _avg(borrow_rows, "borrow_bal_qty", 0, 5) if borrow_rows else None
    avg5_prev = _avg(borrow_rows, "borrow_bal_qty", 5, 10) if borrow_rows else None
    short_today = float(short_latest["short_qty"] or 0) if short_latest else None
    short_avg5 = _avg(short_rows, "short_qty", 0, 5) if short_rows else None
    short_avg5_prev = _avg(short_rows, "short_qty", 5, 10) if short_rows else None
    short_amt_today = float(short_latest["short_amt"] or 0) if short_latest else None
    short_amt_avg5 = _avg(short_rows, "short_amt", 0, 5) if short_rows else None
    short_ratio = short_latest.get("short_volume_ratio") if short_latest else None
    short_amount_ratio = short_latest.get("short_amount_ratio") if short_latest else None
    short_ratio_5d = _weighted_ratio(short_rows, "short_qty", "trade_volume", 0, 5)
    short_ratio_prev5d = _weighted_ratio(short_rows, "short_qty", "trade_volume", 5, 10)
    short_amount_ratio_5d = _weighted_ratio(short_rows, "short_amt", "trade_amount", 0, 5)
    trade_volume = float(short_latest["trade_volume"] or 0) if short_latest else None
    borrow_date = borrow_latest["bas_dt"] if borrow_latest else None
    short_date = short_latest["trade_date"] if short_latest else None
    return {
        "today": today_val, "avg5": avg5, "avg5_prev": avg5_prev,
        "today_signal": _pressure_signal(today_val, avg5),
        "week_signal": _pressure_signal(avg5, avg5_prev),
        "borrow_bal_amt": borrow_latest["borrow_bal_amt"] if borrow_latest else None,
        "borrow_bal_pct": borrow_latest["borrow_bal_pct"] if borrow_latest else None,
        "short_today": short_today,
        "short_avg5": short_avg5,
        "short_avg5_prev": short_avg5_prev,
        "short_amt_today": short_amt_today,
        "short_amt_avg5": short_amt_avg5,
        "short_ratio": short_ratio,
        "short_ratio_5d": short_ratio_5d,
        "short_ratio_prev5d": short_ratio_prev5d,
        "short_amount_ratio": short_amount_ratio,
        "short_amount_ratio_5d": short_amount_ratio_5d,
        "short_today_signal": _pressure_signal(short_ratio, short_ratio_5d),
        "short_week_signal": _pressure_signal(short_ratio_5d, short_ratio_prev5d),
        "trade_volume": trade_volume,
        "borrow_latest_date": borrow_date,
        "short_latest_date": short_date,
        "latest_date": max(d for d in (borrow_date, short_date) if d),
        "short_source": "KIS_FHPST04830000" if short_latest else None,
    }
