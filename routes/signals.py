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
from db_utils import connect_stock_db

logger = logging.getLogger(__name__)
router = APIRouter()

DB_PATH = "stock.db"


def _db():
    return _sl.connect(DB_PATH, timeout=30)


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
            conn = _sl.connect(DB_PATH, timeout=30)
            result = fn(conn, *args)
            conn.commit(); conn.close()
            cache[cache_key] = {"data": result, "at": _t.time()}
        except Exception as e:
            logger.error(f"[백그라운드계산] {cache_key}: {e}")
        finally:
            cache.pop(lock_key, None)
    _thr.Thread(target=_run, daemon=True, name=f"BgCompute-{cache_key}").start()


def _bg_compute_without_db(fn, cache_key):
    """Run a self-contained expensive calculation once without blocking HTTP."""
    import threading as _thr
    cache = _cache()
    lock_key = f"_computing_{cache_key}"
    if cache.get(lock_key):
        return
    cache[lock_key] = True

    def _run():
        try:
            cache[cache_key] = {"data": fn(), "at": _t.time()}
        except Exception as e:
            logger.exception("[백그라운드계산] %s: %s", cache_key, e)
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
    """Return the scheduled, saved market briefing; GET requests never generate it."""
    try:
        from signal_engine import get_market_regime_snapshot
        conn = connect_stock_db(timeout=30, row_factory=_sl.Row)
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
        snap = get_market_regime_snapshot(conn, force_refresh=True)
        conn.close()
        return {"result": gen, "snapshot": snap}
    except Exception as e:
        logger.error(f"[시그널/market-regime/briefing] {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/market-regime/qa")
def get_market_regime_qa(date: str = ""):
    """시장 국면 브리핑 저장 전 QA 로그 조회."""
    try:
        from signal_engine import get_market_regime_qa_summary
        conn = _db()
        data = get_market_regime_qa_summary(conn, qa_date=(date or None), limit=200)
        conn.close()
        return data
    except Exception as e:
        logger.error(f"[시그널/market-regime/qa] {e}")
        return {"qa_date": date or "", "counts": {}, "items": [], "error": str(e)}


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
        _bg_compute(lambda conn, code: calc_stock_signals(code, conn), key, stock_code)
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
    from signal_engine import calc_combo_v2
    _bg_compute(calc_combo_v2, "combo_v2")
    return cached.get("data", [])


# ── GET /api/signals/fin-screener ──────────────────────────────
@router.get("/fin-screener")
def get_fin_screener():
    cache = _cache()
    cached = cache.get("fin_screener", {})
    ttl = 1800 if _is_market_open() else 14400
    if cached and (_t.time() - cached.get("at", 0)) < ttl:
        return cached["data"]
    _bg_compute_without_db(screener.advanced_screening, "fin_screener")
    return cached.get("data", [])


# ── GET /api/signals/high-profit-candidates ─────────────────────
@router.get("/high-profit-candidates")
def get_high_profit_candidates(limit: int = 80, refresh: bool = False):
    """High-profit concentrated logic: core sector + liquidity + near high + insider buy + backlog/order."""
    cache = _cache()
    cached = cache.get("high_profit_candidates", {})
    ttl = 1800 if _is_market_open() else 14400
    if not refresh and cached and (_t.time() - cached.get("at", 0)) < ttl:
        return cached["data"]
    try:
        data = screener.high_profit_compound_screening(limit=limit)
        cache["high_profit_candidates"] = {"data": data, "at": _t.time()}
        return data
    except Exception as e:
        logger.error(f"[고수익 집중 후보] {e}")
        return []


# ── GET /api/trigger-ranking ────────────────────────────────────
@router.get("/trigger-ranking")
def get_trigger_ranking():
    cache     = _cache()
    # 결과 전체 캐시 (TTL: 장중 5분, 장외 30분)
    _tr_ttl = 300 if _is_market_open() else 1800
    _tr_cached = cache.get("trigger_ranking_result", {})
    if _tr_cached and (_t.time() - _tr_cached.get("at", 0)) < _tr_ttl:
        return _tr_cached["data"]

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
    response = {"stocks": result, "cached_at": cached_at, "total": len(result)}
    if result:  # 빈 결과는 캐시하지 않음
        cache["trigger_ranking_result"] = {"data": response, "at": _t.time()}
    return response


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
        conn = _sl.connect(DB_PATH, timeout=30)
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
        conn = _sl.connect(DB_PATH, timeout=30)
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
        conn = _sl.connect(DB_PATH, timeout=30)
        conn.row_factory = _sl.Row
        result = calc_sector_megatrend(conn)
        conn.close()
        cache["v12_sector"] = {"data": result, "at": _t.time()}
        return result
    except Exception as e:
        logger.error(f"[v12] {e}")
        return []


# ── GET /api/signals/kiwoom-conditions ─────────────────────────
@router.get("/kiwoom-conditions")
def get_kiwoom_conditions(strategy: str = "all", refresh: bool = False):
    """
    키움조건식 5가지 퀀트 전략 스크리닝

    strategy: all | value_blue | supply_momentum | growth_garp | high52_break | contrarian
    """
    cache = _cache()
    cache_key = f"kiwoom_cond_{strategy}"
    cached = cache.get(cache_key, {})
    ttl = 3600  # 1시간 캐시

    if not refresh and cached and (_t.time() - cached.get("at", 0)) < ttl:
        return cached["data"]

    # stale-while-revalidate
    if cached and not refresh:
        from signal_engine import calc_kiwoom_conditions
        _bg_compute(calc_kiwoom_conditions, cache_key, strategy)
        return cached["data"]

    try:
        from signal_engine import calc_kiwoom_conditions
        conn = _sl.connect(DB_PATH, timeout=30)
        conn.row_factory = _sl.Row
        result = calc_kiwoom_conditions(conn, strategy)
        conn.close()
        cache[cache_key] = {"data": result, "at": _t.time()}
        return result
    except Exception as e:
        logger.error(f"[kiwoom-conditions] {e}", exc_info=True)
        return {}


# ──────────────────────────────────────────────────────────
# 과열(하락위험) 종목 리스트 — 2026-07-13 실증 기반
# 근거: strategy_feature_snapshot 17만행 6개월 전방수익 라벨 실증 —
#   60일 +100% 급등 직후 종목의 6개월 내 -30% 하락률 37~41% (기준율 10~11%의 3.6배),
#   평균수익 -0.9~-4.7% (시장평균 +6.5~11% 대비 대폭 열위). 시기분리 검증 안정.
# 용도: 매수 회피 리스트 / 보유 시 리스크 점검. V-GC·V12 가상매매에는 진입 제외 필터로 이미 반영.
# ──────────────────────────────────────────────────────────
@router.get("/overheat-risk")
def get_overheat_risk(limit: int = 50):
    """60일 수익률 +100% 초과(과열) 종목 목록 — 6개월 -30% 하락 고위험군."""
    import sqlite3 as _sl
    import time as _t
    cache = _cache()
    ck = "overheat_risk"
    ent = cache.get(ck)
    if ent and _t.time() - ent["at"] < 1800:
        return ent["data"]
    conn = _sl.connect("stock.db")
    conn.row_factory = _sl.Row
    try:
        rows = conn.execute("""
            WITH p AS (
              SELECT stock_code, close, volume,
                     ROW_NUMBER() OVER(PARTITION BY stock_code ORDER BY date DESC) rn
              FROM price_history
              WHERE close > 0 AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
            )
            SELECT a.stock_code, su.stock_name, su.sector_large, su.market_cap,
                   a.close AS cur, b.close AS c60,
                   (a.close / b.close - 1) * 100 AS ret60
            FROM p a
            JOIN p b ON b.stock_code = a.stock_code AND b.rn = 61
            JOIN stock_universe su ON su.stock_code = a.stock_code
            WHERE a.rn = 1 AND b.close > 0
              AND a.close >= b.close * 2.0
              AND su.market_cap >= 300
              -- 감자/병합 미조정 아티팩트 제외: 60일 내 하루 ±50% 이상 점프 종목
              AND NOT EXISTS (
                SELECT 1 FROM (
                  SELECT close, LEAD(close) OVER(ORDER BY rn) nxt
                  FROM p p2 WHERE p2.stock_code = a.stock_code AND p2.rn <= 61
                ) j WHERE j.nxt > 0 AND (j.close / j.nxt > 2.0 OR j.close / j.nxt < 0.5)
              )
            ORDER BY ret60 DESC
            LIMIT ?
        """, (int(limit),)).fetchall()
        items = [{
            "stock_code": r["stock_code"],
            "stock_name": r["stock_name"],
            "sector": r["sector_large"],
            "market_cap_억": r["market_cap"],
            "current_price": r["cur"],
            "ret60_pct": round(r["ret60"], 1),
        } for r in rows]
        result = {
            "count": len(items),
            "items": items,
            "evidence": {
                "signal": "60일 수익률 +100% 초과 (급등 과열)",
                "fwd6m_down30_rate": "37.5%(학습 20~22) / 41.0%(검증 23~25) — 기준율 10.3%/11.4%",
                "fwd6m_avg_return": "-0.9% / -4.7% (시장평균 +11.0%/+6.5% 대비 열위)",
                "source": "strategy_feature_snapshot 6개월 전방수익 라벨 17만행, 2026-07-13 실증",
                "caveat": "확률적 신호 — 개별 종목이 반드시 하락한다는 뜻 아님. 3배 신화가 이어지는 소수(≈7%)도 존재.",
            },
        }
        cache[ck] = {"data": result, "at": _t.time()}
        return result
    finally:
        conn.close()


@router.get("/consensus-revisions")
def get_consensus_revisions(days: int = 60, limit: int = 60):
    """컨센서스 목표주가 최근 상향조정 종목 — 2026-08-23 실험 로드맵 후속 연구.

    배경: consensus_targets가 2024-05~2026-08(2년+)로 누적돼 처음으로 walk-forward
    검증이 가능해짐(기존엔 "데이터 짧아 백테스트 불가"로 미착수 항목이었음).
    학습기(2024-05~2025-05)/검증기(2025-06~2026-05) 분리 검증 결과, 목표주가 괴리율(A)과
    매수의견 여부(C)는 학습·검증 방향이 어긋나 기각됐지만, "직전 대비 목표주가 상향폭"(B)만
    양쪽에서 방향이 일치하고 검증기에서 오히려 격차가 커짐(상위20% vs 하위20% forward60일
    수익률 격차: 학습 +2.96%p → 검증 +5.70%p) — 재현되는 약한 신호로 확인.
    ⚠️ 상관계수 자체는 0.02~0.04로 매우 약함 — 단독 매매 신호가 아니라 참고용 발굴 도구.
    아직 정식 실행 백테스트(포지션 사이징·비용 반영)는 거치지 않았음 — scratch/
    validate_consensus_signal.py 참조.
    """
    import sqlite3 as _sl
    import time as _t
    from datetime import date as _date, timedelta as _timedelta
    cache = _cache()
    ck = f"consensus_revisions_{days}_{limit}"
    ent = cache.get(ck)
    if ent and _t.time() - ent["at"] < 1800:
        return ent["data"]
    # SQLite date('now', ?)는 PostgreSQL 라우팅에서 미지원 — 컷오프를 Python에서 직접 계산
    # (이 프로젝트에서 반복 확인된 함정: db_compat.py가 SQLite 전용 날짜함수를 못 옮김)
    cutoff = (_date.today() - _timedelta(days=abs(days))).isoformat()
    conn = _sl.connect("stock.db")
    conn.row_factory = _sl.Row
    try:
        rows = conn.execute("""
            WITH ranked AS (
                SELECT ct.*, su.sector_large, su.market_cap,
                       ROW_NUMBER() OVER (PARTITION BY ct.stock_code ORDER BY ct.report_date DESC, ct.id DESC) rn
                FROM consensus_targets ct
                LEFT JOIN stock_universe su ON su.stock_code = ct.stock_code
                WHERE ct.target_price > 0 AND ct.prev_target_price > 0
                  AND ct.report_date >= ?
            )
            SELECT stock_code, stock_name, sector_large, market_cap,
                   report_date, securities_firm, opinion, target_price, prev_target_price,
                   ROUND((target_price / prev_target_price - 1) * 100, 1) AS revision_pct
            FROM ranked
            WHERE rn = 1 AND target_price > prev_target_price
              -- 300% 초과 상향은 실제 재평가보다 스크래핑 오류(자릿수 누락 등) 가능성이 높아 제외
              -- (표본조사 결과 249/3616건이 2배 이상 — 대부분 2025~26 반도체 랠리로 설명되나
              -- 소수는 명백한 이상치, 예: 농심 53,000원→570,000원. 방어적으로 상한만 적용)
              AND (target_price / prev_target_price) <= 4.0
            ORDER BY revision_pct DESC
            LIMIT ?
        """, (cutoff, int(limit))).fetchall()
        items = [{
            "stock_code": r["stock_code"],
            "stock_name": r["stock_name"],
            "sector": r["sector_large"],
            "market_cap_억": r["market_cap"],
            "report_date": r["report_date"],
            "securities_firm": r["securities_firm"],
            "opinion": r["opinion"],
            "target_price": r["target_price"],
            "prev_target_price": r["prev_target_price"],
            "revision_pct": r["revision_pct"],
        } for r in rows]
        result = {
            "count": len(items),
            "items": items,
            "evidence": {
                "signal": "직전 대비 목표주가 상향조정 (증권사 리포트 기준)",
                "walkforward": "상위20% vs 하위20% forward60일수익률 격차: 학습기(24-05~25-05) +2.96%p → "
                                "검증기(25-06~26-05) +5.70%p (방향 일치, 검증기에서 강화)",
                "correlation": "학습기 0.019 / 검증기 0.040 (둘 다 양수지만 매우 약함)",
                "source": "consensus_targets 8천여건, scratch/validate_consensus_signal.py 2026-08-23",
                "caveat": "실행 백테스트(비용·포지션사이징) 미검증 — 발굴/참고용, 단독 매매신호로 쓰지 말 것. "
                          "커버리지도 대형/중형주 위주(820종목)로 제한적.",
            },
        }
        cache[ck] = {"data": result, "at": _t.time()}
        return result
    finally:
        conn.close()
