"""
routes/us_sector_rotation.py — 미국(나스닥/S&P500) 주도섹터·주도주 탐지 (2026-09-27 신규)

routes/sector_rotation.py(국내)와 같은 Leading/Improving/Weakening/Lagging 4분면 + 점수/단계
체계를 미국 시장에 적용한다. 국내와 달리 외국인/기관 수급, 수출YoY, DART 실적 데이터가 없어
가격·거래량 기반 팩터(RS 초과수익·거래량비·섹터폭)만 쓰는 순수 모멘텀/RS 로테이션 방식
(StockCharts RRG, IBD 등 실전 섹터 로테이션 도구와 동일 접근).

섹터 = 11개 GICS 유사 섹터(SPDR Select Sector ETF 11종을 섹터 지표/벤치마크로 사용, us_stock_meta.
sector 값과 1:1 매핑). 유니버스는 ?universe=sp500(S&P500 503종목, 기본) 또는 nasdaq(나스닥 상장
전체 약 3,600종목) — us_stock_meta.index_name 필터. 벤치마크는 sp500→SPY, nasdaq→QQQ.

⚠ 국내 "섹터 로테이션"의 리더종목(top-picks)은 저평가·수급유입 초기 반전 후보(52주 "저점" 근처를
가점)인 반면, 여기 리더종목은 이미 신고가권에서 상승 중인 모멘텀 리더(52주 "고점" 근처를 가점,
IBD/오닐 스타일)다 — 국내·미국 시장 성격(수급 주도 vs 추세 주도)이 달라 의도적으로 반대 기준을
쓴다. 화면에서도 이 차이를 안내할 것.

API:
  GET /api/us-sector-rotation/leadership?universe=sp500|nasdaq   # 단계/점수/4분면/리더종목 통합
  GET /api/us-sector-rotation/scores?universe=...                # 점수 랭킹만
  GET /api/us-sector-rotation/rotation-map?universe=...           # 4분면 좌표만
  GET /api/us-sector-rotation/top-picks/{sector}?universe=...     # 섹터 내 리더종목 top N
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from fastapi import APIRouter, Query

from db_compat import connect_primary_db

router = APIRouter()

# 섹터키 → (라벨, 대표 ETF, us_stock_meta.sector 매핑값, 색상)
SECTOR_ETF: Dict[str, Dict] = {
    "technology":              {"label": "💻 기술",          "etf": "XLK",  "meta_sector": "Technology",             "color": "#2563eb"},
    "financial_services":      {"label": "🏦 금융",          "etf": "XLF",  "meta_sector": "Financial Services",     "color": "#64748b"},
    "energy":                  {"label": "⛽ 에너지",         "etf": "XLE",  "meta_sector": "Energy",                 "color": "#b45309"},
    "industrials":             {"label": "🏗 산업재",         "etf": "XLI",  "meta_sector": "Industrials",            "color": "#78716c"},
    "consumer_defensive":      {"label": "🛒 필수소비재",     "etf": "XLP",  "meta_sector": "Consumer Defensive",     "color": "#65a30d"},
    "consumer_cyclical":       {"label": "🚗 임의소비재",     "etf": "XLY",  "meta_sector": "Consumer Cyclical",      "color": "#0ea5e9"},
    "healthcare":              {"label": "💊 헬스케어",       "etf": "XLV",  "meta_sector": "Healthcare",             "color": "#ec4899"},
    "basic_materials":         {"label": "🧪 소재",           "etf": "XLB",  "meta_sector": "Basic Materials",        "color": "#059669"},
    "utilities":               {"label": "⚡ 유틸리티",        "etf": "XLU",  "meta_sector": "Utilities",              "color": "#f59e0b"},
    "real_estate":             {"label": "🏢 리츠/부동산",     "etf": "XLRE", "meta_sector": "Real Estate",            "color": "#8b5cf6"},
    "communication_services":  {"label": "📡 커뮤니케이션",    "etf": "XLC",  "meta_sector": "Communication Services", "color": "#7c3aed"},
}
BENCHMARK = {"sp500": "SPY", "nasdaq": "QQQ"}
INDEX_FILTER = {"sp500": ["S&P500"], "nasdaq": ["S&P500", "NASDAQ"]}  # nasdaq 유니버스는 S&P500도 포함(상장 전체)

_cache: Dict[str, tuple] = {}
_CACHE_TTL = 900  # 15분 — 장중 갱신 빈도, KR 캐시(TTL) 대비 다소 느슨(수급 데이터 없어 변화가 느림)


def _conn():
    conn = connect_primary_db(timeout=30)
    return conn


def _cached(key: str, fn, *args, **kwargs):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    val = fn(*args, **kwargs)
    _cache[key] = (now, val)
    return val


def _pct_change(conn, ticker: str, rn_back: int) -> Optional[float]:
    """ticker의 최신 종가 대비 rn_back 거래일 전 종가 등락률(%). 거래일 인덱스 기반(주말/휴장 영향 없음)."""
    row = conn.execute(
        """
        WITH ranked AS (
            SELECT close, ROW_NUMBER() OVER (ORDER BY date DESC) AS rn
            FROM us_price_history WHERE ticker=? AND close>0
        )
        SELECT MAX(CASE WHEN rn=1 THEN close END) AS latest,
               MAX(CASE WHEN rn=? THEN close END) AS prior
        FROM ranked
        """,
        (ticker, rn_back),
    ).fetchone()
    if not row or not row["latest"] or not row["prior"]:
        return None
    return (row["latest"] - row["prior"]) / row["prior"] * 100


def _vol_ratio(conn, ticker: str) -> Optional[float]:
    """최근 10거래일 평균거래량 / 최근 60거래일 평균거래량."""
    row = conn.execute(
        """
        WITH ranked AS (
            SELECT volume, ROW_NUMBER() OVER (ORDER BY date DESC) AS rn
            FROM us_price_history WHERE ticker=? AND volume>0
        )
        SELECT AVG(CASE WHEN rn<=10 THEN volume END) AS v10,
               AVG(CASE WHEN rn<=60 THEN volume END) AS v60
        FROM ranked WHERE rn<=60
        """,
        (ticker,),
    ).fetchone()
    if not row or not row["v10"] or not row["v60"] or row["v60"] <= 0:
        return None
    return row["v10"] / row["v60"]


def _sector_breadth(conn, meta_sector: str, index_names: List[str]) -> Optional[float]:
    """섹터 구성종목 중 52주 고점 대비 10% 이내('신고가권')인 비율(%) — KR _get_sector_breadth와 같은 정의."""
    ph = ",".join("?" for _ in index_names)
    row = conn.execute(
        f"""
        WITH tickers AS (
            SELECT ticker FROM us_stock_meta WHERE sector=? AND index_name IN ({ph})
        ),
        recent AS (
            SELECT h.ticker, h.close, ROW_NUMBER() OVER (PARTITION BY h.ticker ORDER BY h.date DESC) AS rn
            FROM us_price_history h JOIN tickers t ON t.ticker = h.ticker
            WHERE h.close > 0
        ),
        last260 AS (SELECT * FROM recent WHERE rn <= 260),
        agg AS (
            SELECT ticker, MAX(close) AS hi_252, MAX(CASE WHEN rn=1 THEN close END) AS latest
            FROM last260 GROUP BY ticker
        )
        SELECT COUNT(*) FILTER (WHERE latest IS NOT NULL AND hi_252 IS NOT NULL AND latest >= hi_252*0.9) AS near_high,
               COUNT(*) FILTER (WHERE latest IS NOT NULL AND hi_252 IS NOT NULL) AS total
        FROM agg
        """,
        [meta_sector] + index_names,
    ).fetchone()
    if not row or not row["total"]:
        return None
    return round(row["near_high"] / row["total"] * 100, 1)


def _ladder(value: Optional[float], steps: List[tuple]) -> int:
    """steps=[(threshold, points), ...] 내림차순 — value가 threshold 이상인 첫 구간의 점수."""
    if value is None:
        return 0
    for th, pts in steps:
        if value >= th:
            return pts
    return 0


def _score_sector_us(conn, sect_key: str, universe: str) -> Dict:
    info = SECTOR_ETF[sect_key]
    etf = info["etf"]
    bench = BENCHMARK[universe]

    ret4_etf  = _pct_change(conn, etf, 21)   # 약 4주(20거래일)
    ret12_etf = _pct_change(conn, etf, 61)   # 약 12주(60거래일)
    ret4_bm   = _pct_change(conn, bench, 21)
    ret12_bm  = _pct_change(conn, bench, 61)
    rs4w  = (ret4_etf - ret4_bm)   if (ret4_etf is not None and ret4_bm is not None) else None
    rs12w = (ret12_etf - ret12_bm) if (ret12_etf is not None and ret12_bm is not None) else None
    vol_ratio = _vol_ratio(conn, etf)
    breadth = _sector_breadth(conn, info["meta_sector"], INDEX_FILTER[universe])

    score = 0
    score += _ladder(rs4w,  [(5, 35), (2, 25), (0, 15)])
    score += _ladder(rs12w, [(8, 30), (3, 20), (0, 10)])
    score += _ladder(vol_ratio, [(1.5, 20), (1.2, 12), (1.0, 6)])
    score += _ladder(breadth,   [(50, 15), (30, 9), (15, 4)])

    if score >= 65:
        signal = "BUY"
    elif score >= 40:
        signal = "WATCH"
    else:
        signal = "NEUTRAL"

    phase = (
        "Leading" if (rs4w or 0) > 0 and (rs12w or 0) > 0 else
        "Improving" if (rs4w or 0) > 0 else
        "Weakening" if (rs12w or 0) > 0 else "Lagging"
    )

    return {
        "sector": sect_key, "label": info["label"], "color": info["color"], "etf": etf,
        "score": round(score), "signal": signal, "phase": phase,
        "rs4w": round(rs4w, 1) if rs4w is not None else None,
        "rs12w": round(rs12w, 1) if rs12w is not None else None,
        "detail": {
            "ret_4w_pct": round(ret4_etf, 1) if ret4_etf is not None else None,
            "ret_12w_pct": round(ret12_etf, 1) if ret12_etf is not None else None,
            "vol_ratio": round(vol_ratio, 2) if vol_ratio is not None else None,
            "breadth_pct": breadth,
            "benchmark": bench,
        },
    }


def _entry_stage_us(score_row: Dict, top_leader: int) -> Dict:
    score = score_row["score"]; phase = score_row["phase"]
    rs4 = score_row["rs4w"]; rs12 = score_row["rs12w"]
    short_weak = rs4 is not None and rs4 < 0
    medium_weak = rs12 is not None and rs12 < -5
    deeply_weak = rs12 is not None and rs12 < -15

    if deeply_weak and short_weak:
        return {"stage": "AVOID", "label": "회피", "priority": 5}
    if medium_weak and short_weak:
        return {"stage": "EARLY_WATCH", "label": "초기 관찰", "priority": 2}
    if score >= 65 and phase in ("Leading", "Improving"):
        return {"stage": "ENTRY_NOW", "label": "진입", "priority": 1}
    if score >= 55 and top_leader >= 65 and phase in ("Leading", "Improving"):
        return {"stage": "ENTRY_NOW", "label": "진입", "priority": 1}
    if score >= 45 or phase == "Improving":
        return {"stage": "EARLY_WATCH", "label": "초기 관찰", "priority": 2}
    if phase == "Leading":
        return {"stage": "HOLD_LEADER", "label": "보유/추세", "priority": 3}
    if score <= 20 and phase in ("Weakening", "Lagging"):
        return {"stage": "AVOID", "label": "회피", "priority": 5}
    return {"stage": "WAIT", "label": "대기", "priority": 4}


def _entry_reasons_us(score_row: Dict) -> List[str]:
    d = score_row["detail"]; reasons = []; risks = []
    if score_row["rs12w"] is not None and score_row["rs12w"] <= -5:
        risks.append(f"가격확인 필요: 12주 RS {score_row['rs12w']:.1f}%")
    if score_row["rs4w"] is not None and score_row["rs4w"] < 0:
        risks.append(f"단기 약세: 4주 RS {score_row['rs4w']:.1f}%")
    if score_row["rs4w"] is not None and score_row["rs4w"] >= 3:
        reasons.append(f"4주 RS +{score_row['rs4w']:.1f}%")
    if d.get("vol_ratio") and d["vol_ratio"] >= 1.5:
        reasons.append(f"거래량 {d['vol_ratio']:.1f}배")
    if d.get("breadth_pct") is not None and d["breadth_pct"] >= 40:
        reasons.append(f"섹터폭 {d['breadth_pct']:.0f}% 신고가권")
    if not reasons:
        reasons.append("선행 신호 부족")
    return (risks + reasons)[:4]


def _leader_picks_us(conn, sect_key: str, universe: str, top_n: int = 5) -> List[Dict]:
    """섹터 내 모멘텀 리더종목 — 52주 고점권 + RS초과 + 거래량 증가 (IBD/오닐 스타일, KR과 반대 방향 — 모듈 docstring 참조)."""
    info = SECTOR_ETF[sect_key]
    index_names = INDEX_FILTER[universe]
    bench = BENCHMARK[universe]
    ph = ",".join("?" for _ in index_names)

    rows = conn.execute(
        f"""
        WITH tickers AS (
            SELECT ticker, company_name, market_cap FROM us_stock_meta
            WHERE sector=? AND index_name IN ({ph})
        ),
        recent AS (
            SELECT h.ticker, h.close, h.volume, h.date,
                   ROW_NUMBER() OVER (PARTITION BY h.ticker ORDER BY h.date DESC) AS rn
            FROM us_price_history h JOIN tickers t ON t.ticker = h.ticker
            WHERE h.close > 0
        ),
        last260 AS (SELECT * FROM recent WHERE rn <= 260),
        agg AS (
            SELECT ticker,
                   MAX(CASE WHEN rn=1  THEN close END) AS latest,
                   MAX(CASE WHEN rn=64 THEN close END) AS price_3m_ago,
                   MAX(close) AS hi_252, MIN(close) AS lo_252,
                   AVG(CASE WHEN rn<=10 THEN volume END) AS v10,
                   AVG(volume) AS v60
            FROM last260 GROUP BY ticker
        )
        SELECT t.ticker, t.company_name, t.market_cap, a.latest, a.price_3m_ago, a.hi_252, a.lo_252, a.v10, a.v60
        FROM agg a JOIN tickers t ON t.ticker = a.ticker
        WHERE a.latest IS NOT NULL
        """,
        [info["meta_sector"]] + index_names,
    ).fetchall()

    bm_ret_3m = _pct_change(conn, bench, 64)

    picks = []
    for r in rows:
        d = dict(r)
        latest, p3m, hi, lo = d["latest"], d["price_3m_ago"], d["hi_252"], d["lo_252"]
        ret_3m = (latest - p3m) / p3m * 100 if (p3m and p3m > 0) else None
        pos_52w = (latest - lo) / (hi - lo) * 100 if (hi and lo and hi > lo) else None
        vr = (d["v10"] / d["v60"]) if (d["v10"] and d["v60"]) else None
        rs_3m = (ret_3m - bm_ret_3m) if (ret_3m is not None and bm_ret_3m is not None) else None

        surge = 0
        reasons = []
        if pos_52w is not None and pos_52w >= 90:
            surge += 25; reasons.append("52주 신고가권")
        elif pos_52w is not None and pos_52w >= 75:
            surge += 15
        if rs_3m is not None and rs_3m >= 15:
            surge += 25; reasons.append(f"벤치마크 대비 +{rs_3m:.0f}%")
        elif rs_3m is not None and rs_3m >= 5:
            surge += 12
        if vr is not None and vr >= 1.5:
            surge += 20; reasons.append(f"거래량 {vr:.1f}배")
        elif vr is not None and vr >= 1.2:
            surge += 10
        if ret_3m is not None and ret_3m >= 20:
            surge += 15; reasons.append(f"3M +{ret_3m:.0f}%")
        elif ret_3m is not None and ret_3m < -10:
            surge -= 10

        picks.append({
            "code": d["ticker"], "name": d["company_name"] or d["ticker"],
            "market_cap_달러": round(d["market_cap"]) if d["market_cap"] else None,
            "surge_score": round(surge),
            "ret_3m": round(ret_3m, 1) if ret_3m is not None else None,
            "rs_3m_excess": round(rs_3m, 1) if rs_3m is not None else None,
            "vol_ratio": round(vr, 2) if vr is not None else None,
            "pos_52w_pct": round(pos_52w, 1) if pos_52w is not None else None,
            "reasons": reasons[:3] or ["데이터 확인 필요"],
        })

    picks.sort(key=lambda x: -x["surge_score"])
    return picks[:top_n]


def _leadership_payload(universe: str) -> Dict:
    conn = _conn()
    try:
        sectors = []
        for sect_key in SECTOR_ETF:
            score_row = _score_sector_us(conn, sect_key, universe)
            leaders = _leader_picks_us(conn, sect_key, universe, top_n=3)
            top_leader = max((p["surge_score"] for p in leaders), default=0)
            stage = _entry_stage_us(score_row, top_leader)
            sectors.append({
                **score_row,
                "stage": stage["stage"], "stage_label": stage["label"], "stage_priority": stage["priority"],
                "entry_reasons": _entry_reasons_us(score_row),
                "leaders": leaders,
                "history_recent": [], "peak_signal": None, "latest_buy_signal": None,
            })
        sectors.sort(key=lambda x: (x["stage_priority"], -x["score"], -(x["rs4w"] or -999)))
        return {
            "universe": universe, "benchmark": BENCHMARK[universe],
            "summary": {
                "entry_now": sum(1 for s in sectors if s["stage"] == "ENTRY_NOW"),
                "watch": sum(1 for s in sectors if s["stage"] == "EARLY_WATCH"),
                "leading": sum(1 for s in sectors if s["phase"] == "Leading"),
                "sectors": len(sectors),
            },
            "sectors": sectors,
            "meta": {"as_of": None, "computed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                      "market_status_label": f"미국({'S&P500' if universe=='sp500' else '나스닥'}) · 가격·거래량 기반 RS 로테이션"},
        }
    finally:
        conn.close()


@router.get("/leadership")
def get_us_leadership(universe: str = Query("sp500", pattern="^(sp500|nasdaq)$")):
    return _cached(f"leadership:{universe}", _leadership_payload, universe)


@router.get("/scores")
def get_us_scores(universe: str = Query("sp500", pattern="^(sp500|nasdaq)$")):
    payload = _cached(f"leadership:{universe}", _leadership_payload, universe)
    sectors = sorted(payload["sectors"], key=lambda x: -x["score"])
    return {"universe": universe, "meta": payload["meta"], "sectors": sectors}


@router.get("/rotation-map")
def get_us_rotation_map(universe: str = Query("sp500", pattern="^(sp500|nasdaq)$")):
    payload = _cached(f"leadership:{universe}", _leadership_payload, universe)
    return {
        "universe": universe, "meta": payload["meta"],
        "sectors": [{"sector": s["sector"], "label": s["label"], "color": s["color"],
                     "rs4w": s["rs4w"], "rs12w": s["rs12w"], "phase": s["phase"]} for s in payload["sectors"]],
    }


@router.get("/top-picks/{sector}")
def get_us_top_picks(sector: str, universe: str = Query("sp500", pattern="^(sp500|nasdaq)$")):
    if sector not in SECTOR_ETF:
        return {"error": "unknown sector", "sector": sector}
    conn = _conn()
    try:
        picks = _cached(f"picks:{universe}:{sector}", _leader_picks_us, conn, sector, universe, 10)
        return {"sector": sector, "universe": universe, "picks": picks}
    finally:
        conn.close()


@router.post("/refresh-cache")
def refresh_us_cache():
    _cache.clear()
    for universe in ("sp500", "nasdaq"):
        _cached(f"leadership:{universe}", _leadership_payload, universe)
    return {"ok": True, "computed_at": time.strftime("%Y-%m-%d %H:%M:%S")}
