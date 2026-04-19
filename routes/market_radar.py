"""
routes/market_radar.py — 시장 Radar: 섹터별 해외 선행지표 + 국내 종목 시그널
"""
from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()
logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "stock.db"


def _db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ── 섹터 정의 ──────────────────────────────────────────────────────
SECTORS: dict[str, dict] = {
    "semiconductor": {
        "name": "반도체",
        "emoji": "💾",
        "overseas": [
            {"symbol": "NVDA",   "name": "엔비디아",      "country": "US"},
            {"symbol": "AMD",    "name": "AMD",           "country": "US"},
            {"symbol": "AVGO",   "name": "브로드컴",      "country": "US"},
            {"symbol": "TSM",    "name": "TSMC",          "country": "TW"},
            {"symbol": "AMAT",   "name": "AMAT",          "country": "US"},
            {"symbol": "8035.T", "name": "도쿄일렉트론",   "country": "JP"},
        ],
        "korean_sectors": ["반도체", "반도체장비"],
    },
    "battery": {
        "name": "2차전지",
        "emoji": "🔋",
        "overseas": [
            {"symbol": "TSLA",   "name": "테슬라",        "country": "US"},
            {"symbol": "ALB",    "name": "알베말",        "country": "US"},
            {"symbol": "LTHM",   "name": "리튬아메리카스", "country": "US"},
            {"symbol": "6981.T", "name": "무라타제작소",   "country": "JP"},
        ],
        "korean_sectors": ["2차전지", "배터리"],
    },
    "power_infra": {
        "name": "전력인프라",
        "emoji": "⚡",
        "overseas": [
            {"symbol": "ETN",    "name": "이튼",          "country": "US"},
            {"symbol": "VRT",    "name": "버티브",        "country": "US"},
            {"symbol": "PWR",    "name": "퀀타서비스",    "country": "US"},
            {"symbol": "HUBB",   "name": "허블",          "country": "US"},
        ],
        "korean_sectors": ["전력장비", "전기전자"],
    },
    "pharma": {
        "name": "제약/바이오",
        "emoji": "💊",
        "overseas": [
            {"symbol": "LLY",    "name": "일라이릴리",    "country": "US"},
            {"symbol": "NVO",    "name": "노보노디스크",  "country": "US"},
            {"symbol": "ABBV",   "name": "애브비",        "country": "US"},
            {"symbol": "MRNA",   "name": "모더나",        "country": "US"},
        ],
        "korean_sectors": ["제약", "바이오"],
    },
    "defense": {
        "name": "K방산/우주",
        "emoji": "🚀",
        "overseas": [
            {"symbol": "LMT",    "name": "록히드마틴",    "country": "US"},
            {"symbol": "NOC",    "name": "노스롭그루만",  "country": "US"},
            {"symbol": "RTX",    "name": "RTX",          "country": "US"},
            {"symbol": "HII",    "name": "헌팅턴잉걸스",  "country": "US"},
        ],
        "korean_sectors": ["방산", "항공우주"],
    },
    "shipbuilding": {
        "name": "조선/해운",
        "emoji": "🚢",
        "overseas": [
            {"symbol": "ZIM",    "name": "ZIM해운",       "country": "US"},
            {"symbol": "STNG",   "name": "스코피오탱커",  "country": "US"},
            {"symbol": "SBLK",   "name": "스타벌크",      "country": "US"},
        ],
        "korean_sectors": ["조선", "해운"],
    },
    "energy": {
        "name": "에너지/소재",
        "emoji": "⛽",
        "overseas": [
            {"symbol": "XOM",    "name": "엑슨모빌",      "country": "US"},
            {"symbol": "CVX",    "name": "셰브론",        "country": "US"},
            {"symbol": "FCX",    "name": "프리포트맥모란", "country": "US"},
            {"symbol": "RIO",    "name": "리오틴토",      "country": "US"},
        ],
        "korean_sectors": ["에너지", "화학", "철강"],
    },
}

_radar_cache: dict = {}
_CACHE_TTL = 300  # 5분


def _calc_signal(changes: list[float]) -> str:
    if not changes:
        return "neutral"
    avg = sum(changes) / len(changes)
    if avg >= 1.5:
        return "green"
    if avg <= -1.5:
        return "red"
    if avg >= 0.3:
        return "yellow_up"
    if avg <= -0.3:
        return "yellow_down"
    return "neutral"


def _fetch_overseas_prices(symbols: list[str]) -> dict[str, dict]:
    """yfinance로 해외 종목 1D/1W 변화율 조회."""
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("[Radar] yfinance 미설치 — pip install yfinance")
        return {}

    result: dict[str, dict] = {}
    try:
        tickers = yf.Tickers(" ".join(symbols))
        for sym in symbols:
            try:
                ticker = tickers.tickers.get(sym)
                if ticker is None:
                    continue
                hist = ticker.history(period="10d")
                if hist is None or len(hist) < 2:
                    continue
                curr   = float(hist["Close"].iloc[-1])
                prev1  = float(hist["Close"].iloc[-2])
                prev5  = float(hist["Close"].iloc[max(0, len(hist) - 6)])
                chg_1d = round((curr - prev1) / prev1 * 100, 2) if prev1 > 0 else 0.0
                chg_1w = round((curr - prev5) / prev5 * 100, 2) if prev5 > 0 else 0.0
                result[sym] = {"price": round(curr, 2), "chg_1d": chg_1d, "chg_1w": chg_1w}
            except Exception as e:
                logger.debug(f"[Radar] {sym}: {e}")
    except Exception as e:
        logger.warning(f"[Radar] yfinance 일괄 조회 실패: {e}")
    return result


def _get_korean_top(sector_keys: list[str], limit: int = 8) -> list[dict]:
    """stock_universe + price_history에서 섹터 상위 종목 당일 등락률 조회."""
    if not sector_keys:
        return []
    conn = _db()
    try:
        ph = ",".join("?" * len(sector_keys))
        rows = conn.execute(
            f"""SELECT u.stock_code, u.stock_name, u.sector_large, u.market_cap,
                       MAX(CASE WHEN rn=1 THEN ph.close END) AS close,
                       MAX(CASE WHEN rn=2 THEN ph.close END) AS prev_close
                FROM stock_universe u
                JOIN (
                    SELECT stock_code, close,
                           ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY date DESC) AS rn
                    FROM price_history
                    WHERE close > 0
                      AND stock_code NOT LIKE '%^%'
                ) ph ON u.stock_code = ph.stock_code
                WHERE u.sector_large IN ({ph})
                  AND ph.rn <= 2
                  AND u.market_cap > 0
                GROUP BY u.stock_code
                ORDER BY u.market_cap DESC
                LIMIT ?""",
            sector_keys + [limit],
        ).fetchall()
        result = []
        for r in rows:
            chg = 0.0
            if r["prev_close"] and r["prev_close"] > 0 and r["close"]:
                chg = round((r["close"] - r["prev_close"]) / r["prev_close"] * 100, 2)
            result.append({
                "stock_code": r["stock_code"],
                "stock_name": r["stock_name"],
                "sector":     r["sector_large"],
                "mktcap":     r["market_cap"],
                "close":      r["close"],
                "chg_1d":     chg,
            })
        return result
    finally:
        conn.close()


# ── GET /api/market-radar/sectors ─────────────────────────────────
@router.get("/sectors")
def list_sectors():
    """섹터 목록 반환."""
    return [
        {"key": k, "name": v["name"], "emoji": v["emoji"]}
        for k, v in SECTORS.items()
    ]


# ── GET /api/market-radar/sector/{sector_key} ──────────────────────
@router.get("/sector/{sector_key}")
def get_sector_radar(sector_key: str):
    """
    특정 섹터 해외 선행지표 + 국내 종목 + 시그널.
    캐시 TTL: 5분
    """
    cache_key = f"radar_{sector_key}"
    cached = _radar_cache.get(cache_key)
    if cached and time.time() - cached["at"] < _CACHE_TTL:
        return cached["data"]

    sector = SECTORS.get(sector_key)
    if not sector:
        raise HTTPException(status_code=404, detail=f"Unknown sector: {sector_key}")

    symbols = [s["symbol"] for s in sector["overseas"]]
    prices  = _fetch_overseas_prices(symbols)

    overseas_result = []
    changes_1d: list[float] = []
    for s in sector["overseas"]:
        p = prices.get(s["symbol"], {})
        chg_1d = p.get("chg_1d", 0.0)
        if p:
            changes_1d.append(chg_1d)
        overseas_result.append({
            **s,
            "price":    p.get("price"),
            "chg_1d":   chg_1d,
            "chg_1w":   p.get("chg_1w", 0.0),
            "has_data": bool(p),
        })

    korean_stocks = _get_korean_top(sector["korean_sectors"])
    signal = _calc_signal(changes_1d)

    data = {
        "sector_key":      sector_key,
        "sector_name":     sector["name"],
        "emoji":           sector["emoji"],
        "signal":          signal,
        "overseas_avg_1d": round(sum(changes_1d) / len(changes_1d), 2) if changes_1d else 0.0,
        "overseas":        overseas_result,
        "korean":          korean_stocks,
    }
    _radar_cache[cache_key] = {"data": data, "at": time.time()}
    return data


# ── GET /api/market-radar/all ──────────────────────────────────────
@router.get("/all")
def get_all_radar():
    """전체 섹터 시그널 요약 (해외 종목 avg 1D 변화율 기반)."""
    cache_key = "radar_all"
    cached = _radar_cache.get(cache_key)
    if cached and time.time() - cached["at"] < _CACHE_TTL:
        return cached["data"]

    all_symbols = list({
        s["symbol"]
        for v in SECTORS.values()
        for s in v["overseas"]
    })
    prices = _fetch_overseas_prices(all_symbols)

    result = []
    for key, sector in SECTORS.items():
        changes = [
            prices[s["symbol"]]["chg_1d"]
            for s in sector["overseas"]
            if s["symbol"] in prices
        ]
        result.append({
            "key":    key,
            "name":   sector["name"],
            "emoji":  sector["emoji"],
            "signal": _calc_signal(changes),
            "avg_1d": round(sum(changes) / len(changes), 2) if changes else 0.0,
        })

    data = {"sectors": result, "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    _radar_cache[cache_key] = {"data": data, "at": time.time()}
    return data
