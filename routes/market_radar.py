"""
routes/market_radar.py  —  시장 Radar API

  GET  /api/market-radar/all                        # 전체 섹터 시그널 요약 (탭 배지)
  GET  /api/market-radar/sector/{sector}/detail     # 섹터 세부: 서브섹터별 종목 + 다기간 가격
  POST /api/market-radar/init-semiconductor         # 반도체 회사 목록 초기화 (최초 1회)
  POST /api/market-radar/refresh-cache              # yfinance 시총/PBR/PER 캐시 갱신
  GET  /api/market-radar/export-csv                 # 섹터 종목 CSV 내보내기 (?sector=semiconductor)
  POST /api/market-radar/import-csv                 # CSV 업로드 → DB 갱신 + 가격캐시 새로고침
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
import sqlite3
from pathlib import Path
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, File, Form, Query, UploadFile
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)
router = APIRouter()

# 절대 경로 — CWD와 무관하게 항상 이 파일 기준으로 stock.db 위치 결정
_HERE = Path(__file__).resolve().parent.parent
DB_PATH = str(_HERE / "stock.db")

SECTOR_KEY_MAP: Dict[str, str] = {
    "semiconductor": "반도체",
    "battery":       "2차전지",
    "power_infra":   "전력/인프라",
    "pharma":        "바이오/헬스케어",
    "defense":       "산업재/방산",
    "shipbuilding":  "산업재/조선",
    "energy":        "소재/화학",
}

SECTOR_META: Dict[str, Dict] = {
    "semiconductor": {"name": "반도체/IT",   "emoji": "💾"},
    "battery":       {"name": "2차전지",      "emoji": "🔋"},
    "power_infra":   {"name": "전력/산업재",  "emoji": "⚡"},
    "pharma":        {"name": "제약/바이오",   "emoji": "💊"},
    "defense":       {"name": "K방산/조선",   "emoji": "🚀"},
    "shipbuilding":  {"name": "조선/해운",     "emoji": "🚢"},
    "energy":        {"name": "에너지/소재",   "emoji": "⛽"},
}

# 반도체 밸류체인 핵심 기업 목록 (lv1 = 섹션 헤더, lv2 = Level2 컬럼 표시값)
SEMICONDUCTOR_COMPANIES: List[Dict] = [
    # ── 설계(IP/EDA) ──────────────────────────────
    {"sort_order": 10, "lv1": "설계(IP/EDA)",  "lv2": "설계 소프트웨어",      "company_name": "Cadence",           "ticker": "CDNS",    "country_raw": "US",     "country_flag": "🇺🇸"},
    {"sort_order": 11, "lv1": "설계(IP/EDA)",  "lv2": "설계 소프트웨어",      "company_name": "Synopsys",          "ticker": "SNPS",    "country_raw": "US",     "country_flag": "🇺🇸"},
    {"sort_order": 12, "lv1": "설계(IP/EDA)",  "lv2": "아키텍처 IP",          "company_name": "ARM",               "ticker": "ARM",     "country_raw": "US",     "country_flag": "🇺🇸"},
    # ── 소재/부품 ──────────────────────────────────
    {"sort_order": 20, "lv1": "소재/부품",     "lv2": "기판(Substrate)",      "company_name": "Ibiden",            "ticker": "4062.T",  "country_raw": "JAPAN",  "country_flag": "🇯🇵"},
    {"sort_order": 21, "lv1": "소재/부품",     "lv2": "서비스시스템",          "company_name": "MKS Instruments",   "ticker": "MKSI",    "country_raw": "US",     "country_flag": "🇺🇸"},
    {"sort_order": 22, "lv1": "소재/부품",     "lv2": "실리콘 웨이퍼",        "company_name": "GlobalWafers",      "ticker": "6488.TW", "country_raw": "TAIWAN", "country_flag": "🇹🇼"},
    {"sort_order": 23, "lv1": "소재/부품",     "lv2": "케미칼/필터",          "company_name": "Entegris",          "ticker": "ENTG",    "country_raw": "US",     "country_flag": "🇺🇸"},
    {"sort_order": 24, "lv1": "소재/부품",     "lv2": "케미칼/필터",          "company_name": "CMC Materials",     "ticker": "CCMP",    "country_raw": "US",     "country_flag": "🇺🇸"},
    {"sort_order": 25, "lv1": "소재/부품",     "lv2": "실리콘 웨이퍼",        "company_name": "Shin-Etsu Chemical","ticker": "4063.T",  "country_raw": "JAPAN",  "country_flag": "🇯🇵"},
    {"sort_order": 26, "lv1": "소재/부품",     "lv2": "실리콘 웨이퍼",        "company_name": "SUMCO",             "ticker": "3436.T",  "country_raw": "JAPAN",  "country_flag": "🇯🇵"},
    # ── 전공정 장비 ────────────────────────────────
    {"sort_order": 30, "lv1": "장비/소재",     "lv2": "계측/검사 장비",       "company_name": "Lasertec",          "ticker": "6920.T",  "country_raw": "JAPAN",  "country_flag": "🇯🇵"},
    {"sort_order": 31, "lv1": "장비/소재",     "lv2": "전공정 장비",          "company_name": "Applied Materials", "ticker": "AMAT",    "country_raw": "US",     "country_flag": "🇺🇸"},
    {"sort_order": 32, "lv1": "장비/소재",     "lv2": "전공정 장비",          "company_name": "Lam Research",      "ticker": "LRCX",    "country_raw": "US",     "country_flag": "🇺🇸"},
    {"sort_order": 33, "lv1": "장비/소재",     "lv2": "전공정 장비",          "company_name": "Tokyo Electron",    "ticker": "8035.T",  "country_raw": "JAPAN",  "country_flag": "🇯🇵"},
    {"sort_order": 34, "lv1": "장비/소재",     "lv2": "계측/검사 장비",       "company_name": "KLA",               "ticker": "KLAC",    "country_raw": "US",     "country_flag": "🇺🇸"},
    {"sort_order": 35, "lv1": "장비/소재",     "lv2": "EUV 장비",            "company_name": "ASML",              "ticker": "ASML",    "country_raw": "US",     "country_flag": "🇳🇱"},
    {"sort_order": 36, "lv1": "장비/소재",     "lv2": "후공정 부품",          "company_name": "ISC",               "ticker": "095340",  "country_raw": "KOREA",  "country_flag": "🇰🇷"},
    {"sort_order": 37, "lv1": "장비/소재",     "lv2": "후공정 장비",          "company_name": "한미반도체",         "ticker": "042700",  "country_raw": "KOREA",  "country_flag": "🇰🇷"},
    {"sort_order": 38, "lv1": "장비/소재",     "lv2": "후공정 장비(다이싱)",   "company_name": "Disco",             "ticker": "6146.T",  "country_raw": "JAPAN",  "country_flag": "🇯🇵"},
    {"sort_order": 39, "lv1": "장비/소재",     "lv2": "후공정 장비(테스트)",   "company_name": "Advantest",         "ticker": "6857.T",  "country_raw": "JAPAN",  "country_flag": "🇯🇵"},
    # ── OSAT ───────────────────────────────────────
    {"sort_order": 40, "lv1": "OSAT",         "lv2": "패키징/테스트",        "company_name": "ASE Technology",   "ticker": "ASX",     "country_raw": "TAIWAN", "country_flag": "🇹🇼"},
    {"sort_order": 41, "lv1": "OSAT",         "lv2": "패키징",               "company_name": "Amkor Technology", "ticker": "AMKR",    "country_raw": "US",     "country_flag": "🇺🇸"},
    # ── 파운드리/메모리 ────────────────────────────
    {"sort_order": 50, "lv1": "파운드리/메모리","lv2": "파운드리",             "company_name": "TSMC",             "ticker": "TSM",     "country_raw": "TAIWAN", "country_flag": "🇹🇼"},
    {"sort_order": 51, "lv1": "파운드리/메모리","lv2": "파운드리/메모리",      "company_name": "삼성전자",          "ticker": "005930",  "country_raw": "KOREA",  "country_flag": "🇰🇷"},
    {"sort_order": 52, "lv1": "파운드리/메모리","lv2": "DRAM/NAND",           "company_name": "SK하이닉스",        "ticker": "000660",  "country_raw": "KOREA",  "country_flag": "🇰🇷"},
    {"sort_order": 53, "lv1": "파운드리/메모리","lv2": "DRAM",                "company_name": "Micron Technology","ticker": "MU",      "country_raw": "US",     "country_flag": "🇺🇸"},
]

_TICKER_ALIASES: Dict[str, str] = {
    "KCRA.HE": "KCR.HE",
}
_DELISTED_TICKERS = {"CTLT", "SGEN", "6236.TW", "6967.T"}
_cache: Dict[str, tuple] = {}
_CACHE_TTL = 300


def _db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _now():
    return time.time()


def _norm_ticker(ticker: str | None) -> str:
    t = (ticker or "").strip()
    if not t or t.upper() == "UNLISTED":
        return ""
    t = _TICKER_ALIASES.get(t.upper(), t)
    if re.match(r"^\d{6}(\.(KS|KQ))?$", t, re.I):
        return t[:6]
    return t.upper()


def _period_change(current, base) -> Optional[float]:
    try:
        if current and base and float(base) != 0:
            return round((float(current) - float(base)) / float(base) * 100.0, 2)
    except Exception:
        pass
    return None


def _signal_dot(pct: Optional[float]) -> str:
    if pct is None:
        return "neutral"
    return "up" if pct > 0 else "dn"


def _signal_from_avg(avg: Optional[float]) -> str:
    if avg is None:
        return "neutral"
    if avg >= 2.0:
        return "green"
    if avg >= 0.3:
        return "yellow_up"
    if avg >= -0.3:
        return "neutral"
    if avg >= -2.0:
        return "yellow_down"
    return "red"


def _ensure_radar_tables(conn) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS radar_semiconductor_override (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            sort_order   INTEGER DEFAULT 0,
            lv1          TEXT NOT NULL,
            lv2          TEXT,
            company_name TEXT NOT NULL,
            ticker       TEXT,
            country_raw  TEXT,
            country_flag TEXT,
            lv2_investment_view TEXT,
            company_insight     TEXT
        );

        CREATE TABLE IF NOT EXISTS radar_sector_override (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            sort_order   INTEGER DEFAULT 0,
            lv0          TEXT NOT NULL,
            lv1          TEXT,
            lv2          TEXT,
            company_name TEXT NOT NULL,
            ticker       TEXT,
            country_raw  TEXT,
            country_flag TEXT,
            lv2_investment_view TEXT,
            company_insight     TEXT
        );

        CREATE TABLE IF NOT EXISTS radar_market_cache (
            ticker      TEXT PRIMARY KEY,
            market_cap  REAL,
            per         REAL,
            pbr         REAL,
            updated_at  TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS radar_price_cache (
            ticker      TEXT NOT NULL,
            rn          INTEGER NOT NULL,
            close       REAL,
            trade_date  TEXT,
            PRIMARY KEY (ticker, rn)
        );
    """)
    conn.commit()


def _is_korean_ticker(ticker: str) -> bool:
    return bool(re.match(r"^\d{6}$", ticker))


def _fetch_price_map(conn, tickers: List[str]) -> Dict[str, Dict]:
    """Korean tickers → price_history, foreign tickers → radar_price_cache."""
    if not tickers:
        return {}
    price_map: Dict[str, Dict] = {}

    korean  = [t for t in tickers if _is_korean_ticker(t)]
    foreign = [t for t in tickers if not _is_korean_ticker(t)]

    # ── 국내 주식: price_history ──
    if korean:
        ph = ",".join("?" for _ in korean)
        try:
            rows = conn.execute(f"""
                WITH ranked AS (
                    SELECT stock_code, close,
                           ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY date DESC) AS rn
                    FROM price_history
                    WHERE stock_code IN ({ph}) AND close > 0
                )
                SELECT stock_code,
                       MAX(CASE WHEN rn=1   THEN close END) AS price,
                       MAX(CASE WHEN rn=2   THEN close END) AS price_1d,
                       MAX(CASE WHEN rn=6   THEN close END) AS price_5d,
                       MAX(CASE WHEN rn=11  THEN close END) AS price_10d,
                       MAX(CASE WHEN rn=31  THEN close END) AS price_30d,
                       MAX(CASE WHEN rn=253 THEN close END) AS price_1y
                FROM ranked
                GROUP BY stock_code
            """, korean).fetchall()
            for r in rows:
                d = dict(r)
                price_map[d["stock_code"]] = d
        except Exception as e:
            logger.debug(f"[market-radar] kr price_map error: {e}")

    # ── 해외 주식: radar_price_cache ──
    if foreign:
        ph = ",".join("?" for _ in foreign)
        try:
            rows = conn.execute(f"""
                SELECT ticker,
                       MAX(CASE WHEN rn=1   THEN close END) AS price,
                       MAX(CASE WHEN rn=2   THEN close END) AS price_1d,
                       MAX(CASE WHEN rn=6   THEN close END) AS price_5d,
                       MAX(CASE WHEN rn=11  THEN close END) AS price_10d,
                       MAX(CASE WHEN rn=31  THEN close END) AS price_30d,
                       MAX(CASE WHEN rn=253 THEN close END) AS price_1y
                FROM radar_price_cache
                WHERE ticker IN ({ph})
                GROUP BY ticker
            """, foreign).fetchall()
            for r in rows:
                d = dict(r)
                price_map[d["ticker"]] = d
        except Exception as e:
            logger.debug(f"[market-radar] foreign price_map error: {e}")

    return price_map


def refresh_foreign_prices_sync() -> int:
    """yfinance로 해외 종목 2년치 일별 가격을 radar_price_cache에 저장. 스케줄러용 동기 함수."""
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("[market-radar] yfinance 없음 — 해외 가격 갱신 불가")
        return 0

    conn = _db()
    try:
        _ensure_radar_tables(conn)
        rows = conn.execute("""
            SELECT DISTINCT ticker FROM radar_semiconductor_override WHERE ticker IS NOT NULL AND ticker != ''
            UNION
            SELECT DISTINCT ticker FROM radar_sector_override WHERE ticker IS NOT NULL AND ticker != ''
        """).fetchall()
        raw_tickers = [row["ticker"] for row in rows]
        foreign_tickers = list({_norm_ticker(t) for t in raw_tickers
                                 if _norm_ticker(t) and not _is_korean_ticker(_norm_ticker(t))})

        if not foreign_tickers:
            return 0

        updated = 0
        for ticker in foreign_tickers:
            try:
                hist = yf.Ticker(ticker).history(period="2y", auto_adjust=True)
                if hist.empty:
                    continue
                closes = hist["Close"].dropna()
                if len(closes) == 0:
                    continue
                # Store as ranked rows (rn=1 = most recent)
                conn.execute("DELETE FROM radar_price_cache WHERE ticker = ?", (ticker,))
                rows_to_insert = []
                for rn, (dt, price) in enumerate(
                    zip(closes.index[::-1], closes.values[::-1]),
                    start=1
                ):
                    trade_date = dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)[:10]
                    rows_to_insert.append((ticker, rn, float(price), trade_date))
                    if rn >= 300:
                        break
                conn.executemany(
                    "INSERT OR REPLACE INTO radar_price_cache (ticker, rn, close, trade_date) VALUES (?,?,?,?)",
                    rows_to_insert
                )
                updated += 1
            except Exception as e:
                logger.debug(f"[market-radar] yfinance {ticker}: {e}")

        conn.commit()
        logger.info(f"[market-radar] 해외 가격 갱신 완료: {updated}/{len(foreign_tickers)}개")
        _cache.clear()
        return updated
    except Exception as e:
        logger.error(f"[market-radar] refresh_foreign_prices_sync error: {e}", exc_info=True)
        return 0
    finally:
        conn.close()


def _fetch_market_map(conn, tickers: List[str]) -> Dict[str, Dict]:
    if not tickers:
        return {}
    placeholders = ",".join("?" for _ in tickers)
    market_map: Dict[str, Dict] = {}
    try:
        rows = conn.execute(f"""
            SELECT ticker, market_cap, per, pbr
            FROM radar_market_cache
            WHERE ticker IN ({placeholders})
        """, tickers).fetchall()
        for r in rows:
            d = dict(r)
            market_map[d["ticker"]] = d
    except Exception as e:
        logger.debug(f"[market-radar] market_map error: {e}")

    # 한국 종목(6자리 숫자): stock_universe에서 per/pbr/market_cap 보완
    kr_tickers = [t for t in tickers if t.isdigit() and len(t) == 6]
    if kr_tickers:
        kr_ph = ",".join("?" for _ in kr_tickers)
        try:
            kr_rows = conn.execute(f"""
                SELECT stock_code, market_cap, per, pbr
                FROM stock_universe
                WHERE stock_code IN ({kr_ph})
            """, kr_tickers).fetchall()
            for r in kr_rows:
                code = r["stock_code"]
                existing = market_map.get(code, {})
                market_map[code] = {
                    "ticker":     code,
                    "market_cap": existing.get("market_cap") or r["market_cap"],
                    "per":        existing.get("per") or r["per"],
                    "pbr":        existing.get("pbr") or r["pbr"],
                }
        except Exception as e:
            logger.debug(f"[market-radar] kr_market_map error: {e}")
    return market_map


def _normalize_country(raw: str) -> str:
    r = (raw or "").strip().upper()
    if r in ("KOREA", "한국", "대한민국", "SOUTH KOREA", "KR"):
        return "KR"
    if r in ("US", "USA", "UNITED STATES", "미국"):
        return "US"
    if r in ("JAPAN", "일본", "JP"):
        return "JP"
    if r in ("TAIWAN", "대만", "TW"):
        return "TW"
    if r in ("CHINA", "중국", "CN"):
        return "CN"
    if r in ("NETHERLANDS", "네덜란드", "NL"):
        return "NL"
    return r or "US"


def _build_stock_item(d: Dict, price_map: Dict, market_map: Dict) -> Dict:
    ticker_raw = (d.get("ticker") or "").strip()
    ticker = _norm_ticker(ticker_raw)
    country = _normalize_country(d.get("country_raw") or "")

    pm = price_map.get(ticker, {})
    mm = market_map.get(ticker, {})

    prc    = pm.get("price")
    p1d    = pm.get("price_1d")
    p5d    = pm.get("price_5d")
    p10d   = pm.get("price_10d")
    p30d   = pm.get("price_30d")
    p1y    = pm.get("price_1y")

    chg_1d  = _period_change(prc, p1d)
    chg_5d  = _period_change(prc, p5d)
    chg_10d = _period_change(prc, p10d)
    chg_30d = _period_change(prc, p30d)
    chg_1y  = _period_change(prc, p1y)

    return {
        "symbol":       ticker or ticker_raw or "Unlisted",
        "name":         d.get("company_name") or ticker,
        "lv1":          (d.get("lv1") or "기타").strip(),
        "lv2":          (d.get("lv2") or "").strip(),
        "country":      country,
        "country_flag": d.get("country_flag") or "",
        "price":        prc,
        "price_1d":     p1d,
        "price_5d":     p5d,
        "price_10d":    p10d,
        "price_30d":    p30d,
        "price_1y":     p1y,
        "chg_1d":       chg_1d,
        "chg_5d":       chg_5d,
        "chg_10d":      chg_10d,
        "chg_30d":      chg_30d,
        "chg_1y":       chg_1y,
        "sig_5d":       _signal_dot(chg_5d),
        "sig_10d":      _signal_dot(chg_10d),
        "sig_30d":      _signal_dot(chg_30d),
        "market_cap":   mm.get("market_cap"),
        "per":          mm.get("per"),
        "pbr":          mm.get("pbr"),
        "desc":         (d.get("company_insight") or d.get("lv2_investment_view") or "").strip() or None,
    }


def _build_sector_detail(sector_key: str) -> Dict:
    lv0  = SECTOR_KEY_MAP.get(sector_key, sector_key)
    meta = SECTOR_META.get(sector_key, {"name": lv0, "emoji": "📊"})

    conn = _db()
    try:
        _ensure_radar_tables(conn)

        rows_raw: List[Dict] = []
        if lv0 == "반도체":
            rows = conn.execute("""
                SELECT company_name, ticker, country_raw, country_flag, lv1, lv2,
                       lv2_investment_view, company_insight
                FROM radar_semiconductor_override
                ORDER BY sort_order, id
            """).fetchall()
            rows_raw = [dict(r) for r in rows]
        else:
            rows = conn.execute("""
                SELECT company_name, ticker, country_raw, country_flag, lv1, lv2,
                       lv2_investment_view, company_insight
                FROM radar_sector_override
                WHERE lv0=?
                ORDER BY sort_order, id
            """, (lv0,)).fetchall()
            rows_raw = [dict(r) for r in rows]

        # Collect tickers for batch price/market fetch
        tickers = [_norm_ticker(d.get("ticker", "")) for d in rows_raw]
        tickers = [t for t in tickers if t]

        price_map  = _fetch_price_map(conn, tickers)
        market_map = _fetch_market_map(conn, tickers)

        # Group by lv1 (section header)
        sections_order: List[str] = []
        sections_map: Dict[str, List] = {}
        for d in rows_raw:
            item = _build_stock_item(d, price_map, market_map)
            lv1 = item["lv1"]
            if lv1 not in sections_map:
                sections_map[lv1] = []
                sections_order.append(lv1)
            sections_map[lv1].append(item)

        sections = []
        all_chgs = []
        for lv1 in sections_order:
            stocks = sections_map[lv1]
            chgs = [s["chg_1d"] for s in stocks if s.get("chg_1d") is not None]
            avg = round(sum(chgs) / len(chgs), 2) if chgs else None
            if avg is not None:
                all_chgs.append(avg)
            # 섹션 설명: DB lv2_investment_view 첫 비어있지 않은 값 우선, 없으면 정적 맵
            section_desc = next(
                (s["desc"] for s in stocks if s.get("desc")), None
            )
            sections.append({
                "name":    lv1,
                "signal":  _signal_from_avg(avg),
                "avg_1d":  avg,
                "desc":    section_desc,
                "stocks":  stocks,
            })

        total_avg = round(sum(all_chgs) / len(all_chgs), 2) if all_chgs else None
        return {
            "sector_key":  sector_key,
            "sector_name": meta["name"],
            "emoji":       meta["emoji"],
            "signal":      _signal_from_avg(total_avg),
            "avg_1d":      total_avg,
            "sections":    sections,
        }
    finally:
        conn.close()


# ── GET /api/market-radar/all ────────────────────────────────────
@router.get("/all")
def get_market_radar_all():
    cache_key = "all"
    cached = _cache.get(cache_key)
    if cached and _now() - cached[0] < _CACHE_TTL:
        return cached[1]

    sectors = []
    for key, lv0 in SECTOR_KEY_MAP.items():
        meta = SECTOR_META.get(key, {"name": lv0, "emoji": "📊"})
        conn = _db()
        try:
            _ensure_radar_tables(conn)
            if lv0 == "반도체":
                rows = conn.execute("""
                    SELECT company_name, ticker, country_raw, country_flag, lv1, lv2
                    FROM radar_semiconductor_override
                    ORDER BY sort_order, id LIMIT 30
                """).fetchall()
            else:
                rows = conn.execute("""
                    SELECT company_name, ticker, country_raw, country_flag, lv1, lv2
                    FROM radar_sector_override
                    WHERE lv0=? ORDER BY sort_order, id LIMIT 30
                """, (lv0,)).fetchall()

            tickers = [_norm_ticker(dict(r).get("ticker", "")) for r in rows]
            tickers = [t for t in tickers if t]
            price_map = _fetch_price_map(conn, tickers)

            chgs = []
            for r in rows:
                d = dict(r)
                t = _norm_ticker(d.get("ticker", ""))
                pm = price_map.get(t, {})
                prc = pm.get("price")
                p1d = pm.get("price_1d")
                chg = _period_change(prc, p1d)
                if chg is not None:
                    chgs.append(chg)
            avg_1d = round(sum(chgs) / len(chgs), 2) if chgs else None
            signal = _signal_from_avg(avg_1d)
        except Exception:
            signal = "neutral"
            avg_1d = None
        finally:
            conn.close()

        sectors.append({
            "key":    key,
            "name":   meta["name"],
            "emoji":  meta["emoji"],
            "signal": signal,
            "avg_1d": avg_1d,
        })

    result = {"sectors": sectors}
    _cache[cache_key] = (_now(), result)
    return result


# ── GET /api/market-radar/sector/{sector}/detail ─────────────────
@router.get("/sector/{sector}/detail")
def get_sector_detail(sector: str):
    cache_key = f"detail_{sector}"
    cached = _cache.get(cache_key)
    if cached and _now() - cached[0] < _CACHE_TTL:
        return cached[1]

    try:
        result = _build_sector_detail(sector)
    except Exception as e:
        logger.error(f"[market-radar] sector={sector} error: {e}", exc_info=True)
        meta = SECTOR_META.get(sector, {"name": sector, "emoji": "📊"})
        result = {
            "sector_key":  sector,
            "sector_name": meta["name"],
            "emoji":       meta["emoji"],
            "signal":      "neutral",
            "avg_1d":      None,
            "sections":    [],
        }

    _cache[cache_key] = (_now(), result)
    return result


# ── POST /api/market-radar/init-semiconductor ────────────────────
@router.post("/init-semiconductor")
def init_semiconductor():
    """반도체 기업 목록을 DB에 초기화 (이미 존재하면 스킵)."""
    conn = _db()
    try:
        _ensure_radar_tables(conn)
        existing = conn.execute(
            "SELECT COUNT(*) AS cnt FROM radar_semiconductor_override"
        ).fetchone()["cnt"]
        if existing > 0:
            return {"message": f"이미 {existing}개 기업이 등록되어 있습니다.", "inserted": 0}

        conn.executemany(
            """
            INSERT INTO radar_semiconductor_override
                (sort_order, lv1, lv2, company_name, ticker, country_raw, country_flag)
            VALUES
                (:sort_order, :lv1, :lv2, :company_name, :ticker, :country_raw, :country_flag)
            """,
            SEMICONDUCTOR_COMPANIES,
        )
        conn.commit()
        return {"message": "반도체 기업 목록 초기화 완료", "inserted": len(SEMICONDUCTOR_COMPANIES)}
    finally:
        conn.close()


# ── POST /api/market-radar/refresh-cache ─────────────────────────
@router.post("/refresh-cache")
async def refresh_market_cache(background_tasks: BackgroundTasks):
    """yfinance에서 시총/PBR/PER 캐시 갱신 (백그라운드)."""
    background_tasks.add_task(_do_refresh_cache)
    return {"message": "캐시 갱신 시작 (백그라운드). 완료까지 1-2분 소요됩니다."}


async def _do_refresh_cache():
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("[market-radar] yfinance 없음 — 캐시 갱신 불가")
        return

    conn = _db()
    try:
        _ensure_radar_tables(conn)
        rows = conn.execute("""
            SELECT ticker FROM radar_semiconductor_override WHERE ticker IS NOT NULL AND ticker != ''
            UNION
            SELECT ticker FROM radar_sector_override       WHERE ticker IS NOT NULL AND ticker != ''
        """).fetchall()
        raw_tickers = [row["ticker"] for row in rows]
        tickers = list({_norm_ticker(t) for t in raw_tickers if _norm_ticker(t)})

        async def fetch_one(t: str):
            try:
                # fast_info: market_cap만 빠르게
                fi  = await asyncio.to_thread(lambda: yf.Ticker(t).fast_info)
                mc  = getattr(fi, "market_cap", None)
                # info dict: PER/PBR는 fast_info에 없어 info로 보완
                inf = await asyncio.to_thread(lambda: yf.Ticker(t).info)
                pe  = inf.get("trailingPE") or inf.get("forwardPE")
                pb  = inf.get("priceToBook")
                if not mc:
                    mc = inf.get("marketCap")
                if mc or pe or pb:
                    return (t, float(mc) if mc else None,
                            float(pe) if pe else None, float(pb) if pb else None)
            except Exception as e:
                logger.debug(f"[market-radar] yfinance {t}: {e}")
            return None

        results = await asyncio.gather(*[fetch_one(t) for t in tickers])
        updated = 0
        for r in results:
            if r is None:
                continue
            t, mc, pe, pb = r
            conn.execute("""
                INSERT INTO radar_market_cache (ticker, market_cap, per, pbr, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(ticker) DO UPDATE SET
                    market_cap = excluded.market_cap,
                    per        = excluded.per,
                    pbr        = excluded.pbr,
                    updated_at = excluded.updated_at
            """, (t, mc, pe, pb))
            updated += 1
        conn.commit()
        logger.info(f"[market-radar] 캐시 갱신 완료: {updated}/{len(tickers)}개")
    except Exception as e:
        logger.error(f"[market-radar] refresh cache error: {e}", exc_info=True)
    finally:
        conn.close()
    # 캐시 무효화
    _cache.clear()


# ── GET /api/market-radar/export-csv ─────────────────────────────
@router.get("/export-csv")
def export_csv(sector: str = Query("semiconductor")):
    """섹터 종목 목록을 CSV로 내보내기."""
    lv0 = SECTOR_KEY_MAP.get(sector, sector)
    conn = _db()
    try:
        _ensure_radar_tables(conn)
        if lv0 == "반도체":
            rows = conn.execute("""
                SELECT sort_order, lv1, lv2, company_name, ticker,
                       country_raw, country_flag, company_insight, lv2_investment_view
                FROM radar_semiconductor_override
                ORDER BY sort_order, id
            """).fetchall()
        else:
            rows = conn.execute("""
                SELECT sort_order, lv1, lv2, company_name, ticker,
                       country_raw, country_flag, company_insight, lv2_investment_view
                FROM radar_sector_override
                WHERE lv0=?
                ORDER BY sort_order, id
            """, (lv0,)).fetchall()
    finally:
        conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["sort_order", "lv1", "lv2", "company_name", "ticker",
                     "country_raw", "country_flag", "company_insight", "lv2_investment_view"])
    for r in rows:
        d = dict(r)
        writer.writerow([
            d.get("sort_order", 0), d.get("lv1", ""), d.get("lv2", ""),
            d.get("company_name", ""), d.get("ticker", ""),
            d.get("country_raw", ""), d.get("country_flag", ""),
            d.get("company_insight", ""), d.get("lv2_investment_view", ""),
        ])

    output.seek(0)
    filename = f"{sector}_radar.csv"
    return StreamingResponse(
        iter(["﻿" + output.read()]),  # BOM for Excel UTF-8
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── POST /api/market-radar/import-csv ────────────────────────────
@router.post("/import-csv")
async def import_csv(
    file: UploadFile = File(...),
    sector: str = Form("semiconductor"),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """CSV 파일을 업로드해 DB 갱신. 새 ticker는 가격 캐시도 자동 새로고침."""
    lv0 = SECTOR_KEY_MAP.get(sector, sector)
    content = await file.read()
    try:
        text = content.decode("utf-8-sig")  # BOM 제거
    except UnicodeDecodeError:
        text = content.decode("cp949", errors="replace")

    reader = csv.DictReader(io.StringIO(text))
    rows_in = list(reader)
    if not rows_in:
        return {"inserted": 0, "updated": 0, "detail": "CSV가 비어 있습니다."}

    conn = _db()
    try:
        _ensure_radar_tables(conn)
        if lv0 == "반도체":
            table = "radar_semiconductor_override"
            extra_cols = ""
            extra_vals = ""
        else:
            table = "radar_sector_override"
            extra_cols = ", lv0"
            extra_vals = f", '{lv0}'"

        inserted = updated = 0
        for row in rows_in:
            sort_order   = int(row.get("sort_order") or 0)
            lv1          = (row.get("lv1") or "").strip()
            lv2          = (row.get("lv2") or "").strip()
            company_name = (row.get("company_name") or "").strip()
            ticker       = (row.get("ticker") or "").strip()
            country_raw  = (row.get("country_raw") or "").strip()
            country_flag = (row.get("country_flag") or "").strip()
            ci           = (row.get("company_insight") or "").strip()
            lv2iv        = (row.get("lv2_investment_view") or "").strip()

            if not company_name:
                continue

            # Upsert by company_name + lv1
            existing = conn.execute(
                f"SELECT id FROM {table} WHERE company_name=? AND lv1=?",
                (company_name, lv1),
            ).fetchone()

            if existing:
                conn.execute(
                    f"""UPDATE {table} SET sort_order=?, lv2=?, ticker=?,
                        country_raw=?, country_flag=?, company_insight=?, lv2_investment_view=?
                        WHERE id=?""",
                    (sort_order, lv2, ticker, country_raw, country_flag, ci, lv2iv, existing["id"]),
                )
                updated += 1
            else:
                conn.execute(
                    f"""INSERT INTO {table}
                        (sort_order, lv1, lv2, company_name, ticker,
                         country_raw, country_flag, company_insight, lv2_investment_view{extra_cols})
                        VALUES (?,?,?,?,?,?,?,?,?{extra_vals})""",
                    (sort_order, lv1, lv2, company_name, ticker,
                     country_raw, country_flag, ci, lv2iv),
                )
                inserted += 1

        conn.commit()
    finally:
        conn.close()

    # 새 종목이 추가됐으면 백그라운드에서 가격 캐시 갱신
    if inserted > 0:
        background_tasks.add_task(refresh_foreign_prices_sync)

    _cache.clear()
    return {"inserted": inserted, "updated": updated}
