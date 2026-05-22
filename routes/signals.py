"""
routes/signals.py — 시그널 보드 + 스크리너 + 진입트리거 API

  GET  /api/signals/market
  GET  /api/signals/stock/{stock_code}
  GET  /api/signals/trend-candidates
  GET  /api/signals/value-candidates
  GET  /api/screener/meta
  GET  /api/signals/combo-candidates
  GET  /api/signals/fin-screener
  GET  /api/trigger-ranking
  GET/PUT/POST/DELETE /api/signals/config
  POST /api/signals/manual/{config_id}
"""

import logging
import sqlite3 as _sl
import time as _t
from collections import defaultdict
from datetime import date as _date

import screener
from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter()

DB_PATH = "stock.db"


def _db():
    return _sl.connect(DB_PATH)


def _is_market_open() -> bool:
    """KST 기준 한국 장 시간 여부 (평일 09:00~15:35)."""
    try:
        from datetime import datetime as _dt
        try:
            import zoneinfo
            kst = _dt.now(zoneinfo.ZoneInfo("Asia/Seoul"))
        except ImportError:
            import pytz
            kst = _dt.now(pytz.timezone("Asia/Seoul"))
        if kst.weekday() >= 5:
            return False
        t = kst.hour * 100 + kst.minute
        return 900 <= t <= 1535
    except Exception:
        return False


def _cache():
    """main._signal_cache 참조 (지연 임포트로 순환 방지)."""
    import main as _m
    return _m._signal_cache


def _bg_compute(fn, cache_key, *args):
    """백그라운드에서 계산 후 캐시 저장. 중복 실행 방지."""
    import threading as _thr
    lock_key = f"_computing_{cache_key}"
    cache = _cache()
    if cache.get(lock_key):
        return
    cache[lock_key] = True
    def _run():
        try:
            conn = _sl.connect(DB_PATH)
            result = fn(conn, *args) if not args else fn(*args)
            conn.commit(); conn.close()
            cache[cache_key] = {"data": result, "at": _t.time()}
        except Exception as e:
            logger.error(f"[백그라운드계산] {cache_key}: {e}")
        finally:
            cache.pop(lock_key, None)
    _thr.Thread(target=_run, daemon=True, name=f"BgCompute-{cache_key}").start()


# ── GET /api/signals/market ─────────────────────────────────────
@router.get("/market")
def get_market_signals(refresh: bool = False):
    cache = _cache()
    cached = cache.get("market", {})
    ttl = 3600 if _is_market_open() else 14400
    is_fresh = cached and (_t.time() - cached.get("at", 0)) < ttl

    if not refresh and is_fresh:
        return cached["data"]

    # 캐시 만료/없음: 보유 중인 stale 데이터 즉시 반환하고 백그라운드에서 갱신
    if cached and not refresh:
        from signal_engine import calc_market_signals
        _bg_compute(calc_market_signals, "market")
        return cached["data"]  # stale 즉시 반환

    # 캐시 완전 미존재(최초 요청) 또는 ?refresh=true: 동기 계산
    try:
        from signal_engine import calc_market_signals
        conn = _db()
        results = calc_market_signals(conn)
        conn.commit(); conn.close()
        cache["market"] = {"data": results, "at": _t.time()}
        return results
    except Exception as e:
        logger.error(f"[시그널/시장] {e}")
        return []


@router.get("/market-regime")
def get_market_regime():
    """5단계 시장 국면 점수 + 최신 AI 브리핑 조회."""
    try:
        from signal_engine import get_market_regime_snapshot, generate_market_ai_briefings
        conn = _db()
        # self-heal: 오늘 브리핑이 없으면 조회 시 1회 생성 시도
        today = _date.today().isoformat()
        row = conn.execute(
            "SELECT COUNT(*) FROM market_signal_briefing WHERE briefing_date=?",
            (today,),
        ).fetchone()
        cnt = int(row[0] or 0) if row else 0
        if cnt < 2:
            try:
                generate_market_ai_briefings(conn)
            except Exception:
                logger.exception("[시그널/market-regime] today briefing self-heal failed")
        data = get_market_regime_snapshot(conn)
        conn.close()
        return data
    except Exception as e:
        logger.error(f"[시그널/market-regime] {e}")
        return {"generated_at": "", "markets": [], "briefings": [], "error": str(e)}


@router.post("/market-regime/briefing")
def generate_market_regime_briefing():
    """시장 국면 AI 브리핑 즉시 생성(수동 트리거)."""
    try:
        from signal_engine import generate_market_ai_briefings, get_market_regime_snapshot
        conn = _db()
        gen = generate_market_ai_briefings(conn)
        snap = get_market_regime_snapshot(conn)
        conn.close()
        return {"result": gen, "snapshot": snap}
    except Exception as e:
        logger.error(f"[시그널/market-regime/briefing] {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── GET /api/signals/stock/{stock_code} ────────────────────────
@router.get("/stock/{stock_code}")
def get_stock_signals(stock_code: str, refresh: bool = False):
    cache = _cache()
    key    = f"stock_{stock_code}"
    cached = cache.get(key, {})
    ttl = 3600 if _is_market_open() else 14400
    is_fresh = cached and (_t.time() - cached.get("at", 0)) < ttl

    if not refresh and is_fresh:
        return cached["data"]

    # stale 즉시 반환 + 백그라운드 갱신
    if cached and not refresh:
        from signal_engine import calc_stock_signals
        _bg_compute(calc_stock_signals, key, stock_code)
        return cached["data"]

    # 완전 캐시 미존재: 동기 계산
    try:
        from signal_engine import calc_stock_signals
        conn = _db()
        results = calc_stock_signals(stock_code, conn)
        conn.commit(); conn.close()
        cache[key] = {"data": results, "at": _t.time()}
        return results
    except Exception as e:
        logger.error(f"[시그널/종목] {stock_code}: {e}")
        return []


# ── GET /api/signals/trend-candidates ──────────────────────────
@router.get("/trend-candidates")
def get_trend_candidates():
    cache = _cache()
    cached = cache.get("trend_candidates", {})
    ttl = 3600 if _is_market_open() else 14400
    if cached and (_t.time() - cached.get("at", 0)) < ttl:
        return cached["data"]
    try:
        from signal_engine import calc_trend_candidates
        conn = _db()
        results = calc_trend_candidates(conn)
        conn.close()
        cache["trend_candidates"] = {"data": results, "at": _t.time()}
        return results
    except Exception as e:
        logger.error(f"[추세후보] {e}")
        return []


# ── GET /api/signals/value-candidates ──────────────────────────
@router.get("/value-candidates")
def get_value_candidates():
    cache = _cache()
    cached = cache.get("value_candidates", {})
    if cached and (_t.time() - cached.get("at", 0)) < 14400:
        return cached["data"]
    try:
        from signal_engine import calc_value_candidates
        conn = _db()
        results = calc_value_candidates(conn)
        conn.close()
        cache["value_candidates"] = {"data": results, "at": _t.time()}
        return results
    except Exception as e:
        logger.error(f"[가치매수후보] {e}")
        return []


# ── GET /api/screener/meta ──────────────────────────────────────
@router.get("/meta")
def get_screener_meta(screener_id: str = ""):
    from signal_logic import get_all_meta, get_screener_meta as _get
    return _get(screener_id) if screener_id else get_all_meta()


# ── GET /api/signals/combo-candidates ──────────────────────────
@router.get("/combo-candidates")
def get_combo_candidates():
    cache = _cache()
    cached = cache.get("combo_candidates", {})
    ttl = 1800 if _is_market_open() else 14400
    if cached and (_t.time() - cached.get("at", 0)) < ttl:
        return cached["data"]
    return []


# ── GET /api/signals/combo-v2 ──────────────────────────────────
@router.get("/combo-v2")
def get_combo_v2():
    """Logic-#2: 수급 주도 모멘텀 스크리너."""
    cache = _cache()
    cached = cache.get("combo_v2", {})
    ttl = 1800 if _is_market_open() else 14400
    if cached and (_t.time() - cached.get("at", 0)) < ttl:
        return cached["data"]
    # 캐시 없으면 즉시 계산 (처음 요청 시)
    try:
        from signal_engine import calc_combo_v2
        conn = _sl.connect(DB_PATH)
        result = calc_combo_v2(conn)
        conn.close()
        cache["combo_v2"] = {"data": result, "at": _t.time()}
        return result
    except Exception as e:
        logger.error(f"[combo-v2] {e}")
        return []


# ── GET /api/signals/fin-screener ──────────────────────────────
@router.get("/fin-screener")
def get_fin_screener():
    cache = _cache()
    cached = cache.get("fin_screener", {})
    ttl = 1800 if _is_market_open() else 14400
    if cached and (_t.time() - cached.get("at", 0)) < ttl:
        return cached["data"]
    return screener.advanced_screening()


# ── GET /api/trigger-ranking ────────────────────────────────────
@router.get("/trigger-ranking")
def get_trigger_ranking():
    cache     = _cache()
    cached_at = cache.get("top20_candidates", {}).get("at", 0)
    top20     = cache.get("top20_candidates", {}).get("data", [])
    value_map = {s["stock_code"]: s for s in cache.get("value_candidates", {}).get("data", [])}
    fin_map   = {s["stock_code"]: s for s in cache.get("fin_screener",     {}).get("data", [])}

    if not top20:
        import threading, main as _main
        threading.Thread(target=_main._run_screener_precompute, daemon=True).start()
        try:
            from signal_engine import calc_top20_candidates
            conn = _db()
            top20 = calc_top20_candidates(conn)
            conn.close()
            cache["top20_candidates"] = {"data": top20, "at": _t.time()}
            cached_at = _t.time()
        except Exception as e:
            logger.error(f"[trigger-ranking 온디맨드] {e}")
            return {"stocks": [], "cached_at": 0, "note": "스크리너 계산 대기 중"}
        if not top20:
            return {"stocks": [], "cached_at": 0, "note": "후보 종목 없음"}

    codes      = [s["stock_code"] for s in top20]
    code_in    = ",".join(f"'{c}'" for c in codes)

    def _to_억(qty, amt, close):
        qty = qty or 0; amt = amt or 0; close = close or 0
        if amt == 0: return qty / 100
        if qty and abs(amt) / abs(qty) >= 0.1: return qty * close / 100_000_000
        return amt / 100

    def _bavg(lst, s, e):
        sl = lst[s:e]
        return sum(sl) / len(sl) if sl else 0

    conn = _db()
    try:
        sup_rows = conn.execute(
            f"SELECT stock_code,date,close,frn_net_buy,inst_net_buy,frn_net_buy_amt,inst_net_buy_amt "
            f"FROM price_history WHERE stock_code IN ({code_in}) AND close>0 "
            f"ORDER BY stock_code, date DESC"
        ).fetchall()
        sup_by_code: dict = defaultdict(list)
        for r in sup_rows:
            if len(sup_by_code[r[0]]) < 6:
                sup_by_code[r[0]].append(r)

        # Need at most 2 rows per stock (current + previous close)
        price_rows = conn.execute(
            f"SELECT stock_code, close FROM price_history "
            f"WHERE stock_code IN ({code_in}) AND close>0 ORDER BY stock_code, date DESC"
            f" LIMIT {len(codes) * 2}"
        ).fetchall()
        price_by_code: dict = {}
        for r in price_rows:
            if r[0] not in price_by_code:
                price_by_code[r[0]] = [r[1]]
            elif len(price_by_code[r[0]]) < 2:
                price_by_code[r[0]].append(r[1])

        bor_rows = conn.execute(
            f"SELECT stock_code, borrow_bal_qty FROM short_sell_daily "
            f"WHERE stock_code IN ({code_in}) AND borrow_bal_qty IS NOT NULL "
            f"ORDER BY stock_code, bas_dt DESC"
        ).fetchall()
        bor_by_code: dict = defaultdict(list)
        for r in bor_rows:
            if len(bor_by_code[r[0]]) < 60:
                bor_by_code[r[0]].append(r[1] or 0)
    finally:
        conn.close()

    result = []
    for stock in top20:
        code   = stock["stock_code"]
        v      = value_map.get(code, {})
        f      = fin_map.get(code, {})
        rows6  = sup_by_code.get(code, [])
        valid5 = [r for r in rows6 if any(r[i] for i in (3, 4, 5, 6))][:5]
        tr     = rows6[0] if rows6 else None
        bors   = bor_by_code.get(code, [])
        prices = price_by_code.get(code, [])

        frn_today  = round(_to_억(tr[3], tr[5], tr[2])) if tr and (tr[3] or tr[5]) else None
        inst_today = round(_to_억(tr[4], tr[6], tr[2])) if tr and (tr[4] or tr[6]) else None
        frn_5d     = round(sum(_to_억(r[3], r[5], r[2]) for r in valid5)) if valid5 else None
        inst_5d    = round(sum(_to_억(r[4], r[6], r[2]) for r in valid5)) if valid5 else None
        change_pct = round((prices[0] - prices[1]) / prices[1] * 100, 2) if len(prices) >= 2 and prices[1] else 0.0
        b5   = _bavg(bors, 0, 5);  b5p = _bavg(bors, 5, 10)
        b10  = _bavg(bors, 0, 10); b30 = _bavg(bors, 0, 30); b30p = _bavg(bors, 30, 60)

        result.append({
            "stock_code": code, "stock_name": stock["stock_name"],
            "market": stock.get("market", ""), "price": prices[0] if prices else 0,
            "change_pct": change_pct, "mktcap": stock.get("mktcap", 0),
            "pbr": stock.get("pbr"), "per": stock.get("per"), "sector": stock.get("sector", ""),
            "frn_today": frn_today, "inst_today": inst_today, "frn_5d": frn_5d, "inst_5d": inst_5d,
            "bor_5d": round(b5), "bor_5d_prev": round(b5p),
            "bor_10d": round(b10), "bor_30d": round(b30), "bor_30d_prev": round(b30p),
            "score": stock.get("score", 0), "track_a": stock.get("track_a", 0),
            "track_b": stock.get("track_b", 0), "sector_bonus": stock.get("sector_bonus", 0),
            "signal": stock.get("signal", "gray"), "detail": stock.get("detail", ""),
            "rsi": stock.get("rsi", 0), "vol_ratio": stock.get("vol_ratio", 0),
            "from_high": stock.get("from_high52", 0), "graham_iv": stock.get("graham_iv"),
            "discount": stock.get("discount"),
            "in_trend": stock.get("track_a", 0) >= 3,
            "in_value": code in value_map or stock.get("track_b", 0) >= 2,
            "in_fin": code in fin_map,
            "value_score": v.get("score", 0),
            "fin_score": f.get("total_score", f.get("score", 0)),
            "combo_count": sum([
                stock.get("track_a", 0) >= 3,
                stock.get("track_b", 0) >= 2,
                code in fin_map,
            ]),
        })

    result.sort(key=lambda x: x["score"], reverse=True)
    return {"stocks": result, "cached_at": cached_at, "total": len(result)}


# ── /api/signals/config CRUD ────────────────────────────────────
@router.get("/config")
def get_signal_configs():
    conn = _db()
    rows = conn.execute(
        "SELECT id,scope,name,label,description,logic_type,params,weight,is_active,sort_order "
        "FROM signal_config ORDER BY scope,sort_order"
    ).fetchall()
    conn.close()
    keys = ["id","scope","name","label","description","logic_type","params","weight","is_active","sort_order"]
    return [{**dict(zip(keys, r)), "is_active": bool(r[8])} for r in rows]


@router.put("/config/{config_id}")
def update_signal_config(config_id: int, payload: dict):
    allowed = ["label","description","params","weight","is_active","sort_order"]
    sets = [f"{k}=?" for k in allowed if k in payload]
    vals = [payload[k] for k in allowed if k in payload]
    if not sets:
        raise HTTPException(status_code=400, detail="수정할 필드 없음")
    conn = _db()
    conn.execute(f"UPDATE signal_config SET {','.join(sets)} WHERE id=?", [*vals, config_id])
    conn.commit(); conn.close()
    return {"status": "ok"}


@router.post("/config")
def add_signal_config(payload: dict):
    conn = _db()
    conn.execute(
        "INSERT INTO signal_config (scope,name,label,description,logic_type,params,weight,sort_order) VALUES (?,?,?,?,?,?,?,?)",
        (payload.get("scope","stock"), payload.get("name","custom"), payload.get("label","새 시그널"),
         payload.get("description",""), payload.get("logic_type","manual"),
         payload.get("params","{}"), payload.get("weight",1), payload.get("sort_order",99))
    )
    conn.commit(); conn.close()
    return {"status": "ok"}


@router.delete("/config/{config_id}")
def delete_signal_config(config_id: int):
    conn = _db()
    conn.execute("UPDATE signal_config SET is_active=0 WHERE id=?", (config_id,))
    conn.commit(); conn.close()
    return {"status": "ok"}


@router.post("/manual/{config_id}")
def set_manual_signal(config_id: int, payload: dict):
    conn = _db()
    conn.execute(
        "INSERT OR REPLACE INTO signal_result (config_id,stock_code,signal,value,description,calc_date) VALUES (?,?,?,?,?,?)",
        (config_id, "", payload.get("signal","yellow"), payload.get("value"),
         payload.get("description",""), _date.today().isoformat())
    )
    conn.commit(); conn.close()
    return {"status": "ok"}


# ── GET /api/signals/v10-earnings-explosion ─────────────────────
@router.get("/v10-earnings-explosion")
def get_v10_earnings_explosion():
    """V10: 이익 폭발 + 매출 급성장 (에이피알·삼양식품 유형)"""
    cache = _cache()
    cached = cache.get("v10_earnings", {})
    ttl = 14400  # 4시간 캐시
    if cached and (_t.time() - cached.get("at", 0)) < ttl:
        return cached["data"]
    try:
        from signal_engine import calc_earnings_explosion
        conn = _sl.connect(DB_PATH)
        conn.row_factory = _sl.Row
        result = calc_earnings_explosion(conn)
        conn.close()
        cache["v10_earnings"] = {"data": result, "at": _t.time()}
        return result
    except Exception as e:
        logger.error(f"[v10] {e}")
        return []


# ── GET /api/signals/v11-turnaround ────────────────────────────
@router.get("/v11-turnaround")
def get_v11_turnaround():
    """V11: 흑자전환 모멘텀 (이수페타시스·엘앤에프 유형)"""
    cache = _cache()
    cached = cache.get("v11_turnaround", {})
    ttl = 14400
    if cached and (_t.time() - cached.get("at", 0)) < ttl:
        return cached["data"]
    try:
        from signal_engine import calc_turnaround_momentum
        conn = _sl.connect(DB_PATH)
        conn.row_factory = _sl.Row
        result = calc_turnaround_momentum(conn)
        conn.close()
        cache["v11_turnaround"] = {"data": result, "at": _t.time()}
        return result
    except Exception as e:
        logger.error(f"[v11] {e}")
        return []


# ── GET /api/signals/v12-sector-megatrend ──────────────────────
@router.get("/v12-sector-megatrend")
def get_v12_sector_megatrend():
    """V12: 섹터 대세 상승 (효성중공업·LS Electric 유형)"""
    cache = _cache()
    cached = cache.get("v12_sector", {})
    ttl = 7200  # 2시간 캐시
    if cached and (_t.time() - cached.get("at", 0)) < ttl:
        return cached["data"]
    try:
        from signal_engine import calc_sector_megatrend
        conn = _sl.connect(DB_PATH)
        conn.row_factory = _sl.Row
        result = calc_sector_megatrend(conn)
        conn.close()
        cache["v12_sector"] = {"data": result, "at": _t.time()}
        return result
    except Exception as e:
        logger.error(f"[v12] {e}")
        return []
