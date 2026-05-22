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
DB_PATH    = str(_HERE / "stock.db")
ETF_DB_PATH = str(_HERE / "ETF_check" / "etf_check.db")
# 해외 종목 가격 DB — 국내 종목은 stock.db, 해외 종목은 us_market.db
US_DB_PATH = str(_HERE.parent / "us_market_dashboard" / "us_market.db")

SECTOR_KEY_MAP: Dict[str, str] = {
    "semiconductor": "반도체",
    "battery":       "2차전지",
    "power_infra":   "전력산업",
    "nuclear":       "원자력",
    "pharma":        "바이오/헬스케어",
    "defense":       "K-방산",
    "construction":  "산업재/건설",
    "shipbuilding":  "조선/해양",
    "shipping":      "해운",
    "automotive":    "자동차",
    "energy":        "소재/화학",
    "steel":         "철강/비철금속",
    "it_hardware":   "IT/하드웨어",
    "telecom":       "통신/플랫폼",
    "finance":       "금융/지주",
}

SECTOR_META: Dict[str, Dict] = {
    "semiconductor": {"name": "반도체/IT",    "emoji": "💾"},
    "battery":       {"name": "2차전지",       "emoji": "🔋"},
    "power_infra":   {"name": "전력산업",      "emoji": "⚡"},
    "nuclear":       {"name": "원자력",         "emoji": "☢️"},
    "pharma":        {"name": "바이오/헬스케어","emoji": "💊"},
    "defense":       {"name": "K방산",          "emoji": "🚀"},
    "construction":  {"name": "산업재/건설",    "emoji": "🏗️"},
    "shipbuilding":  {"name": "조선",           "emoji": "🚢"},
    "shipping":      {"name": "해운",           "emoji": "🛳️"},
    "automotive":    {"name": "자동차",         "emoji": "🚗"},
    "energy":        {"name": "소재/화학",      "emoji": "⛽"},
    "steel":         {"name": "철강/비철금속",  "emoji": "⚙️"},
    "it_hardware":   {"name": "IT/하드웨어",    "emoji": "💻"},
    "telecom":       {"name": "통신/플랫폼",    "emoji": "📡"},
    "finance":       {"name": "금융/지주",      "emoji": "🏦"},
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

    # ── 해외 주식: us_market.db의 us_price_history (date-based, ROW_NUMBER CTE) ──
    if foreign:
        ph = ",".join("?" for _ in foreign)
        us_conn = None
        try:
            us_conn = sqlite3.connect(US_DB_PATH, timeout=30)
            us_conn.row_factory = sqlite3.Row
            rows = us_conn.execute(f"""
                WITH ranked AS (
                    SELECT ticker, close,
                           ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) AS rn
                    FROM us_price_history
                    WHERE ticker IN ({ph}) AND close > 0
                )
                SELECT ticker,
                       MAX(CASE WHEN rn=1   THEN close END) AS price,
                       MAX(CASE WHEN rn=2   THEN close END) AS price_1d,
                       MAX(CASE WHEN rn=6   THEN close END) AS price_5d,
                       MAX(CASE WHEN rn=11  THEN close END) AS price_10d,
                       MAX(CASE WHEN rn=31  THEN close END) AS price_30d,
                       MAX(CASE WHEN rn=253 THEN close END) AS price_1y
                FROM ranked
                GROUP BY ticker
            """, foreign).fetchall()
            for r in rows:
                d = dict(r)
                price_map[d["ticker"]] = d
        except Exception as e:
            logger.debug(f"[market-radar] us_db price_map error: {e}")
        finally:
            if us_conn:
                try: us_conn.close()
                except: pass

    return price_map


def refresh_foreign_prices_sync() -> int:
    """yfinance로 해외 종목 2년치 일별 가격을 us_market.db의 us_price_history에 저장.
    국내 종목은 stock.db, 해외 종목은 us_market.db에 분리 저장. 스케줄러용 동기 함수."""
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("[market-radar] yfinance 없음 — 해외 가격 갱신 불가")
        return 0

    conn    = _db()
    us_conn = None
    try:
        _ensure_radar_tables(conn)
        # 해외 종목 목록 조회 (radar_*_override 테이블에서)
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

        us_conn = sqlite3.connect(US_DB_PATH, timeout=60)

        try:
            from api_rate_limiter import api_limiter as _rl
        except ImportError:
            _rl = None

        updated = 0
        for ticker in foreign_tickers:
            # Yahoo Finance rate limit — 차단 방지
            if _rl:
                if not _rl.wait("YAHOO"):
                    logger.warning("[market-radar] Yahoo Finance 일일 쿼터 소진 — 루프 중단")
                    break
            else:
                import time as _t; _t.sleep(1.5)

            try:
                hist = yf.Ticker(ticker).history(period="2y", auto_adjust=True)
                if hist.empty:
                    continue
                closes = hist["Close"].dropna()
                if len(closes) == 0:
                    continue
                # us_price_history에 날짜 기준으로 저장 (INSERT OR REPLACE)
                us_conn.execute("DELETE FROM us_price_history WHERE ticker = ?", (ticker,))
                rows_to_insert = []
                for dt, price in zip(closes.index, closes.values):
                    trade_date = dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)[:10]
                    rows_to_insert.append((ticker, trade_date, float(price), float(price)))
                us_conn.executemany(
                    "INSERT OR REPLACE INTO us_price_history(ticker, date, close, adj_close) VALUES (?,?,?,?)",
                    rows_to_insert
                )
                updated += 1
            except Exception as e:
                logger.debug(f"[market-radar] yfinance {ticker}: {e}")
                if _rl and "429" in str(e):
                    _rl.report_block("YAHOO", cooldown=300)

        us_conn.commit()
        logger.info(f"[market-radar] 해외 가격 갱신 완료 → us_market.db: {updated}/{len(foreign_tickers)}개")
        _cache.clear()
        return updated
    except Exception as e:
        logger.error(f"[market-radar] refresh_foreign_prices_sync error: {e}", exc_info=True)
        return 0
    finally:
        conn.close()
        if us_conn:
            try: us_conn.close()
            except: pass


def _fetch_market_map(conn, tickers: List[str]) -> Dict[str, Dict]:
    if not tickers:
        return {}
    placeholders = ",".join("?" for _ in tickers)
    market_map: Dict[str, Dict] = {}

    # 1) radar_market_cache (모든 종목 – 캐시된 값)
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

    # 2) 한국 종목(6자리 숫자): stock_universe에서 per/pbr/market_cap 보완
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

    # 3) 해외 종목: us_universe에서 보완 (market_cap/per/pbr 캐시 누락 시)
    foreign_tickers = [t for t in tickers if not (t.isdigit() and len(t) == 6)]
    missing_foreign = [t for t in foreign_tickers if t not in market_map or not market_map[t].get("market_cap")]
    if missing_foreign:
        us_conn = None
        try:
            us_conn = sqlite3.connect(US_DB_PATH, timeout=30)
            us_conn.row_factory = sqlite3.Row
            mf_ph = ",".join("?" for _ in missing_foreign)
            us_rows = us_conn.execute(f"""
                SELECT ticker, market_cap, per, pbr
                FROM us_universe
                WHERE ticker IN ({mf_ph})
            """, missing_foreign).fetchall()
            for r in us_rows:
                ticker = r["ticker"]
                existing = market_map.get(ticker, {})
                market_map[ticker] = {
                    "ticker":     ticker,
                    "market_cap": existing.get("market_cap") or r["market_cap"],
                    "per":        existing.get("per") or r["per"],
                    "pbr":        existing.get("pbr") or r["pbr"],
                }
        except Exception as e:
            logger.debug(f"[market-radar] us_universe market_map error: {e}")
        finally:
            if us_conn:
                try: us_conn.close()
                except: pass

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


_FX_CACHE: Dict[str, Any] = {}   # {rates: {country: rate}, at: float}
_FX_TTL = 3600  # 1시간 캐시

def _get_fx_rates() -> Dict[str, float]:
    """price_history에서 최신 환율(→KRW) 조회. 없으면 yfinance로 즉시 조회."""
    global _FX_CACHE
    if _FX_CACHE and (time.time() - _FX_CACHE.get("at", 0)) < _FX_TTL:
        return _FX_CACHE["rates"]

    fx_map = {"US": 1470.0, "JP": 9.8, "TW": 46.0, "NL": 1620.0, "DE": 1620.0, "CN": 205.0, "HK": 190.0}
    # stock_code, factor (JPY:100엔 단위로 저장됨 → 1엔당 = /100)
    tickers_info = [
        ("USDKRW=X", "US",   1.0),
        ("JPYKRW=X", "JP",   1.0),   # price_history의 JPY/KRW는 이미 1엔당 환율
        ("TWDKRW=X", "TW",   1.0),
        ("EURKRW=X", "NL",   1.0),   # NL, DE 모두 EUR
        ("HKDKRW=X", "HK",   1.0),
    ]
    conn = _db()
    try:
        for ticker, country, factor in tickers_info:
            row = conn.execute(
                "SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 1",
                (ticker,)
            ).fetchone()
            if row and row[0]:
                fx_map[country] = float(row[0]) * factor
                if country == "NL":
                    fx_map["DE"] = fx_map["NL"]
    except Exception as e:
        logger.debug(f"[market-radar] fx_rates DB error: {e}")
    finally:
        conn.close()

    # DB에 JPY/TWD 없으면 yfinance fallback
    missing = [t for t, c, _ in tickers_info if c not in fx_map or fx_map[c] == {"JP":9.8,"TW":46.0,"NL":1620.0,"DE":1620.0,"CN":205.0,"HK":190.0}.get(c, 0)]
    if missing:
        try:
            import yfinance as yf
            for ticker, country, factor in tickers_info:
                if ticker in missing:
                    info = yf.Ticker(ticker).fast_info
                    rate = getattr(info, "last_price", None)
                    if rate:
                        fx_map[country] = float(rate) * factor
                        if country == "NL":
                            fx_map["DE"] = fx_map["NL"]
        except Exception as e:
            logger.debug(f"[market-radar] fx_rates yfinance error: {e}")

    _FX_CACHE = {"rates": fx_map, "at": time.time()}
    return fx_map


def _to_krw(market_cap: Optional[float], country: str, fx: Dict[str, float]) -> Optional[float]:
    """외화 시총 → KRW 변환. KR은 그대로 반환."""
    if market_cap is None:
        return None
    if country == "KR":
        return market_cap
    rate = fx.get(country)
    if not rate:
        return None
    return market_cap * rate


def _build_stock_item(d: Dict, price_map: Dict, market_map: Dict, fx: Optional[Dict] = None) -> Dict:
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
        "market_cap":     mm.get("market_cap"),
        "market_cap_krw": _to_krw(mm.get("market_cap"), country, fx or {}),
        "per":            mm.get("per"),
        "pbr":            mm.get("pbr"),
        "desc":           (d.get("company_insight") or "").strip() or None,
        "lv2_view":       (d.get("lv2_investment_view") or "").strip() or None,
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
                       lv2_investment_view, company_insight, lv0_industry_overview
                FROM radar_semiconductor_override
                ORDER BY sort_order, id
            """).fetchall()
            rows_raw = [dict(r) for r in rows]
        else:
            rows = conn.execute("""
                SELECT company_name, ticker, country_raw, country_flag, lv1, lv2,
                       lv2_investment_view, company_insight, lv0_industry_overview
                FROM radar_sector_override
                WHERE lv0=?
                ORDER BY sort_order, id
            """, (lv0,)).fetchall()
            rows_raw = [dict(r) for r in rows]

        # 섹터/LV1 인사이트 (sector_insights 테이블)
        try:
            # LV0 매핑 역방향: DB lv0 → sector_insights lv0
            SI_LV0_RMAP = {v: k for k, v in {
                '반도체': '반도체', '2차전지': '2차전지', '전력산업': '전력/인프라',
                '자동차': '자동차', '바이오/헬스케어': '바이오', '조선/해양': '조선/해운',
            }.items()}
            si_lv0 = SI_LV0_RMAP.get(lv0, lv0)
            insights_rows = conn.execute("""
                SELECT lv1, lv2, insight_type, description FROM sector_insights
                WHERE lv0=? OR lv0=?
                ORDER BY id
            """, (lv0, si_lv0)).fetchall()
            sector_insights_list = [dict(r) for r in insights_rows]
        except Exception:
            sector_insights_list = []

        # LV0 overview 추출
        sector_overview = next(
            (r.get("lv0_industry_overview") for r in rows_raw if r.get("lv0_industry_overview")),
            None
        )
        if not sector_overview:
            for ins in sector_insights_list:
                if "Level 0" in (ins.get("insight_type") or ""):
                    sector_overview = ins["description"]
                    break

        # Collect tickers for batch price/market fetch
        tickers = [_norm_ticker(d.get("ticker", "")) for d in rows_raw]
        tickers = [t for t in tickers if t]

        price_map  = _fetch_price_map(conn, tickers)
        market_map = _fetch_market_map(conn, tickers)
        fx_rates   = _get_fx_rates()

        # Group by lv1 (section header)
        sections_order: List[str] = []
        sections_map: Dict[str, List] = {}
        for d in rows_raw:
            item = _build_stock_item(d, price_map, market_map, fx_rates)
            lv1 = item["lv1"]
            if lv1 not in sections_map:
                sections_map[lv1] = []
                sections_order.append(lv1)
            sections_map[lv1].append(item)

        # lv1별 insight 매핑
        lv1_insight_map: Dict[str, str] = {}
        for ins in sector_insights_list:
            if "Level 1" in (ins.get("insight_type") or ""):
                key = ins.get("lv1") or ""
                if key and key not in lv1_insight_map:
                    lv1_insight_map[key] = ins["description"]

        sections = []
        all_chgs = []
        for lv1 in sections_order:
            stocks = sections_map[lv1]
            chgs = [s["chg_1d"] for s in stocks if s.get("chg_1d") is not None]
            avg = round(sum(chgs) / len(chgs), 2) if chgs else None
            if avg is not None:
                all_chgs.append(avg)
            # 섹션 설명: sector_insights lv1 description 우선, 없으면 DB lv2_investment_view
            section_desc = lv1_insight_map.get(lv1) or next(
                (s["desc"] for s in stocks if s.get("desc") and len(s["desc"]) > 30), None
            )
            sections.append({
                "name":    lv1,
                "signal":  _signal_from_avg(avg),
                "avg_1d":  avg,
                "desc":    section_desc,
                "stocks":  stocks,
            })

        total_avg = round(sum(all_chgs) / len(all_chgs), 2) if all_chgs else None

        # 가격 최신 업데이트 날짜: radar_price_cache MAX(trade_date) 또는 price_history MAX(date)
        try:
            all_tickers = [t for t in tickers if t]
            foreign_t = [t for t in all_tickers if not _is_korean_ticker(t)]
            korean_t  = [t for t in all_tickers if _is_korean_ticker(t)]
            dates = []
            if foreign_t:
                ph = ",".join("?" for _ in foreign_t)
                r = conn.execute(f"SELECT MAX(trade_date) FROM radar_price_cache WHERE ticker IN ({ph})", foreign_t).fetchone()
                if r and r[0]: dates.append(r[0])
            if korean_t:
                ph = ",".join("?" for _ in korean_t)
                r = conn.execute(f"SELECT MAX(date) FROM price_history WHERE stock_code IN ({ph})", korean_t).fetchone()
                if r and r[0]: dates.append(r[0])
            updated_date = max(dates) if dates else None
        except Exception:
            updated_date = None

        return {
            "sector_key":      sector_key,
            "sector_name":     meta["name"],
            "emoji":           meta["emoji"],
            "signal":          _signal_from_avg(total_avg),
            "avg_1d":          total_avg,
            "updated_date":    updated_date,
            "sector_overview": sector_overview,
            "sector_insights": sector_insights_list,
            "sections":        sections,
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


# ═══════════════════════════════════════════════════════════════════
# 반도체 밸류스트림 — 전종목 테이블 (엑셀 마스터 기반)
# ═══════════════════════════════════════════════════════════════════

DEFAULT_REF_DATES = ["2026-01-02", "2025-08-01", "2025-04-02"]


def _normalize_ref_date(v: Optional[str], fallback: str) -> str:
    s = (v or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    return fallback

@router.get("/semiconductor/valuestream")
def get_semiconductor_valuestream(
    ref1: Optional[str] = Query(None, description="기준일1 YYYY-MM-DD"),
    ref2: Optional[str] = Query(None, description="기준일2 YYYY-MM-DD"),
    ref3: Optional[str] = Query(None, description="기준일3 YYYY-MM-DD"),
):
    """반도체 전종목 밸류스트림 테이블.
    A-F: semiconductor_valuestream 테이블 (기업명·Lv1·Lv2·고객·주요업)
    G~: stock_universe(시총·PBR·PER), price_history(현재가·TTM매출)
    기준일 가격 3종 + 현재가 대비 변동률
    """
    ref_dates = [
        _normalize_ref_date(ref1, DEFAULT_REF_DATES[0]),
        _normalize_ref_date(ref2, DEFAULT_REF_DATES[1]),
        _normalize_ref_date(ref3, DEFAULT_REF_DATES[2]),
    ]

    conn = _db()
    try:
        # 1. 마스터 데이터
        rows = conn.execute("""
            SELECT sv.sort_order, sv.stock_code, sv.company_name, sv.lv1, sv.lv2,
                   sv.customers, sv.main_business, sv.ticker_raw, sv.etf_flag
            FROM semiconductor_valuestream sv
            ORDER BY sv.sort_order
        """).fetchall()

        if not rows:
            return {"rows": [], "ref_dates": ref_dates}

        codes = [r[1] for r in rows if r[1]]
        ph    = ",".join("?" * len(codes))

        # 2. 현재가 (최신 close)
        price_map = {}
        for r in conn.execute(
            f"SELECT stock_code, close FROM price_history WHERE stock_code IN ({ph})"
            " AND close > 0 AND date = (SELECT MAX(date) FROM price_history p2"
            "  WHERE p2.stock_code = price_history.stock_code AND p2.close > 0)",
            codes,
        ):
            price_map[r[0]] = r[1]

        # 2b. fallback: 최신 close 기준 서브쿼리가 느릴 수 있으니 간단 버전으로 재시도
        if not price_map:
            for r in conn.execute(
                f"SELECT stock_code, close FROM price_history"
                f" WHERE stock_code IN ({ph}) AND close > 0"
                f" GROUP BY stock_code HAVING date = MAX(date)",
                codes,
            ):
                price_map[r[0]] = r[1]

        # 3. stock_universe (시총·PBR·PER·섹터)
        universe_map = {}
        for r in conn.execute(
            f"SELECT stock_code, market_cap, pbr, per, sector_small, market FROM stock_universe"
            f" WHERE stock_code IN ({ph})",
            codes,
        ):
            mktcap_raw = r[1]
            # stock_universe.market_cap 단위: 원(won) → 억원 변환 (÷1억)
            mktcap_억 = round(mktcap_raw / 1e8) if mktcap_raw else None
            universe_map[r[0]] = {"market_cap": mktcap_억, "pbr": r[2], "per": r[3],
                                  "sector": r[4], "market": r[5]}

        # 4. TTM 매출 (최근 4분기 합산)
        ttm_map = {}
        for r in conn.execute(
            f"""SELECT stock_code, SUM(revenue) as ttm
                FROM (
                  SELECT stock_code, revenue,
                         ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY year DESC, quarter DESC) as rn
                  FROM financial_data
                  WHERE stock_code IN ({ph}) AND is_annual = 0 AND revenue IS NOT NULL
                )
                WHERE rn <= 4
                GROUP BY stock_code
                HAVING COUNT(*) >= 1""",
            codes,
        ):
            # financial_data.revenue 단위: 원(won) → 억원 변환
            ttm_map[r[0]] = round(r[1] / 1e8) if r[1] else None

        # 5. 기준일 가격 (price_history에서 해당 날짜 또는 직전 영업일)
        ref_price_map: Dict[str, Dict[str, float]] = {c: {} for c in codes}
        for ref_date in ref_dates:
            # 해당 날짜 포함 이전 5영업일 중 가장 최근 종가
            for r in conn.execute(
                f"""SELECT stock_code, close FROM price_history
                    WHERE stock_code IN ({ph}) AND date <= ? AND close > 0
                    GROUP BY stock_code HAVING date = MAX(date)""",
                codes + [ref_date],
            ):
                ref_price_map[r[0]][ref_date] = r[1]

        # 5b. 전일 대비 당일 변동률
        day_change_map: Dict[str, Optional[float]] = {}
        for r in conn.execute(
            f"""WITH ranked AS (
                    SELECT stock_code, close,
                           ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY date DESC) AS rn
                    FROM price_history
                    WHERE stock_code IN ({ph}) AND close > 0
                )
                SELECT stock_code,
                       MAX(CASE WHEN rn=1 THEN close END) AS cur_close,
                       MAX(CASE WHEN rn=2 THEN close END) AS prev_close
                FROM ranked
                GROUP BY stock_code""",
            codes,
        ):
            cur_close = r["cur_close"]
            prev_close = r["prev_close"]
            if cur_close and prev_close and float(prev_close) != 0:
                day_change_map[r["stock_code"]] = round((float(cur_close) - float(prev_close)) / float(prev_close) * 100.0, 2)
            else:
                day_change_map[r["stock_code"]] = None

        # 5c. ETF 편입금액(억) 최신값
        etf_amount_map: Dict[str, Optional[float]] = {}
        try:
            econn = sqlite3.connect(ETF_DB_PATH, timeout=10)
            econn.row_factory = sqlite3.Row
            eph = ",".join("?" * len(codes))
            for rr in econn.execute(
                f"""WITH latest AS (
                        SELECT e.stock_code, e.etf_amount,
                               ROW_NUMBER() OVER (PARTITION BY e.stock_code ORDER BY e.trade_date DESC) AS rn
                        FROM etf_inclusion_daily e
                        WHERE e.stock_code IN ({eph})
                          AND e.etf_amount IS NOT NULL
                          AND e.etf_amount > 0
                    )
                    SELECT stock_code, etf_amount
                    FROM latest
                    WHERE rn = 1""",
                codes,
            ):
                etf_amount_map[rr["stock_code"]] = rr["etf_amount"]
            econn.close()
        except Exception as _e:
            logger.debug(f"[market-radar] etf amount map error: {_e}")

        # 6. 조합
        result = []
        for row in rows:
            sort_order, code, cname, lv1, lv2, customers, main_biz, ticker_raw, etf_flag = row
            uni  = universe_map.get(code, {})
            cur  = price_map.get(code)
            mktcap = uni.get("market_cap")  # 억원
            ttm_rev = ttm_map.get(code)

            ref_prices = {}
            ref_chgs   = {}
            for rd in ref_dates:
                rp = ref_price_map.get(code, {}).get(rd)
                ref_prices[rd] = rp
                if cur and rp and rp > 0:
                    ref_chgs[rd] = round((cur - rp) / rp * 100, 2)
                else:
                    ref_chgs[rd] = None

            psr = None
            if mktcap and ttm_rev and ttm_rev > 0:
                psr = round(mktcap / ttm_rev, 2)

            etf_amount = etf_amount_map.get(code)
            etf_ratio_pct = None
            if etf_amount and mktcap and mktcap > 0:
                etf_ratio_pct = round(float(etf_amount) / float(mktcap) * 100.0, 2)

            result.append({
                "sort_order":   sort_order,
                "stock_code":   code,
                "company_name": cname,
                "lv1":          lv1,
                "lv2":          lv2,
                "customers":    customers,
                "main_business": main_biz,
                "ticker_raw":   ticker_raw,
                "market":       uni.get("market"),
                "sector":       uni.get("sector"),
                "price":        cur,
                "day_change_pct": day_change_map.get(code),
                "market_cap":   mktcap,
                "etf_amount":   etf_amount,
                "etf_ratio_pct": etf_ratio_pct,
                "pbr":          uni.get("pbr"),
                "per":          uni.get("per"),
                "ttm_revenue":  round(ttm_rev) if ttm_rev else None,
                "psr":          psr,
                "ref_prices":   ref_prices,
                "ref_chgs":     ref_chgs,
            })

        return {"rows": result, "ref_dates": ref_dates}
    finally:
        conn.close()


@router.post("/semiconductor/valuestream/refresh")
def refresh_semiconductor_valuestream():
    """semiconductor_valuestream 테이블 기준일 가격 재계산 (별도 저장 없이 API 호출 시 자동 최신화)."""
    return {"status": "ok", "note": "API 호출 시마다 price_history에서 실시간 계산됩니다."}


@router.get("/semiconductor/summary")
def get_semiconductor_summary(
    start_date: Optional[str] = Query(None, description="시작일 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="종료일 YYYY-MM-DD"),
):
    """반도체 카테고리(LV1) 종합현황 집계.
    - 주가 변동률: 카테고리 내 종목의 평균 변동률(시작일 대비 종료일)
    - 상승종목수: 종료일 기준 전일 대비 상승 종목 수
    - 코스피/나스닥 대비: 카테고리 평균 변동률과 지수 변동률 비교
    """
    conn = _db()
    try:
        latest = conn.execute(
            "SELECT MAX(date) AS d FROM price_history WHERE stock_code='^KS11' AND close > 0"
        ).fetchone()
        latest_date = latest["d"] if latest and latest["d"] else DEFAULT_REF_DATES[0]
        sd = _normalize_ref_date(start_date, DEFAULT_REF_DATES[2])
        ed = _normalize_ref_date(end_date, latest_date)

        # 종목 + 카테고리
        base_rows = conn.execute("""
            SELECT stock_code, company_name, lv1
            FROM semiconductor_valuestream
            WHERE stock_code IS NOT NULL AND stock_code != ''
            ORDER BY sort_order
        """).fetchall()
        if not base_rows:
            return {"rows": [], "start_date": sd, "end_date": ed}

        codes = [r["stock_code"] for r in base_rows]
        ph = ",".join("?" * len(codes))

        # 경량화: 종목별 상관 서브쿼리로 시작/종료/최신/전일 가격 조회
        price_rows = conn.execute(
            f"""
            SELECT c.stock_code,
                   (SELECT close FROM price_history p WHERE p.stock_code=c.stock_code AND p.date<=? AND p.close>0 ORDER BY p.date DESC LIMIT 1) AS start_close,
                   (SELECT close FROM price_history p WHERE p.stock_code=c.stock_code AND p.date<=? AND p.close>0 ORDER BY p.date DESC LIMIT 1) AS end_close,
                   (SELECT close FROM price_history p WHERE p.stock_code=c.stock_code AND p.close>0 ORDER BY p.date DESC LIMIT 1) AS cur_close,
                   (SELECT close FROM price_history p WHERE p.stock_code=c.stock_code AND p.close>0 ORDER BY p.date DESC LIMIT 1 OFFSET 1) AS prev_close
            FROM (SELECT DISTINCT stock_code FROM semiconductor_valuestream WHERE stock_code IS NOT NULL AND stock_code!='') c
            """,
            (sd, ed),
        ).fetchall()
        pmap: Dict[str, Dict[str, Optional[float]]] = {}
        for r in price_rows:
            pmap[r["stock_code"]] = {
                "start": r["start_close"],
                "end": r["end_close"],
                "cur": r["cur_close"],
                "prev": r["prev_close"],
            }

        # PSR (기존 로직과 동일: 시총/TTM매출)
        uni_map: Dict[str, Optional[float]] = {}
        for r in conn.execute(
            f"SELECT stock_code, market_cap FROM stock_universe WHERE stock_code IN ({ph})",
            codes,
        ):
            uni_map[r["stock_code"]] = (round(r["market_cap"] / 1e8) if r["market_cap"] else None)

        ttm_map: Dict[str, Optional[float]] = {}
        for r in conn.execute(
            f"""SELECT stock_code, SUM(revenue) AS ttm
                FROM (
                    SELECT stock_code, revenue,
                           ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY year DESC, quarter DESC) AS rn
                    FROM financial_data
                    WHERE stock_code IN ({ph}) AND is_annual = 0 AND revenue IS NOT NULL
                )
                WHERE rn <= 4
                GROUP BY stock_code""",
            codes,
        ):
            ttm_map[r["stock_code"]] = (round(r["ttm"] / 1e8) if r["ttm"] else None)

        # 지수 시작/종료 변동률
        def _index_change(sym: str) -> Optional[float]:
            rs = conn.execute(
                """SELECT close FROM price_history
                   WHERE stock_code=? AND date <= ? AND close > 0
                   ORDER BY date DESC LIMIT 1""",
                (sym, sd),
            ).fetchone()
            re_ = conn.execute(
                """SELECT close FROM price_history
                   WHERE stock_code=? AND date <= ? AND close > 0
                   ORDER BY date DESC LIMIT 1""",
                (sym, ed),
            ).fetchone()
            if not rs or not re_:
                return None
            s0 = rs["close"]
            e0 = re_["close"]
            if not s0:
                return None
            return round((e0 - s0) / s0 * 100.0, 2)

        kospi_chg = _index_change("^KS11")
        nasdaq_chg = _index_change("^IXIC")

        # 카테고리 집계
        grp: Dict[str, Dict[str, Any]] = {}
        for r in base_rows:
            code = r["stock_code"]
            lv1 = r["lv1"] or "기타"
            g = grp.setdefault(lv1, {
                "sector": lv1,
                "count": 0,
                "sum_chg": 0.0,
                "chg_n": 0,
                "up_count": 0,
                "sum_psr": 0.0,
                "psr_n": 0,
            })
            g["count"] += 1

            pp = pmap.get(code, {})
            s_close = pp.get("start")
            e_close = pp.get("end")
            if s_close and e_close and float(s_close) != 0:
                c = (float(e_close) - float(s_close)) / float(s_close) * 100.0
                g["sum_chg"] += c
                g["chg_n"] += 1

            cur = pp.get("cur")
            prev = pp.get("prev")
            if cur and prev and float(cur) > float(prev):
                g["up_count"] += 1

            mkt = uni_map.get(code)
            ttm = ttm_map.get(code)
            if mkt and ttm and ttm > 0:
                g["sum_psr"] += (float(mkt) / float(ttm))
                g["psr_n"] += 1

        rows = []
        total_count = 0
        total_up = 0
        total_chg_sum = 0.0
        total_chg_n = 0
        total_psr_sum = 0.0
        total_psr_n = 0

        for i, lv1 in enumerate(sorted(grp.keys()), start=1):
            g = grp[lv1]
            avg_chg = round(g["sum_chg"] / g["chg_n"], 2) if g["chg_n"] > 0 else None
            up_ratio = round(g["up_count"] / g["count"] * 100.0, 1) if g["count"] > 0 else 0.0
            avg_psr = round(g["sum_psr"] / g["psr_n"], 2) if g["psr_n"] > 0 else None
            rows.append({
                "no": i,
                "sector": lv1,
                "avg_change_pct": avg_chg,
                "stock_count": g["count"],
                "up_count": g["up_count"],
                "up_ratio_pct": up_ratio,
                "avg_psr": avg_psr,
                "vs_kospi": "outperform" if (avg_chg is not None and kospi_chg is not None and avg_chg >= kospi_chg) else "underperform",
                "vs_nasdaq": "outperform" if (avg_chg is not None and nasdaq_chg is not None and avg_chg >= nasdaq_chg) else "underperform",
            })
            total_count += g["count"]
            total_up += g["up_count"]
            total_chg_sum += g["sum_chg"]
            total_chg_n += g["chg_n"]
            total_psr_sum += g["sum_psr"]
            total_psr_n += g["psr_n"]

        total_avg_chg = round(total_chg_sum / total_chg_n, 2) if total_chg_n > 0 else None
        total_up_ratio = round(total_up / total_count * 100.0, 1) if total_count > 0 else 0.0
        total_avg_psr = round(total_psr_sum / total_psr_n, 2) if total_psr_n > 0 else None

        summary_row = {
            "no": 0,
            "sector": "종합",
            "avg_change_pct": total_avg_chg,
            "stock_count": total_count,
            "up_count": total_up,
            "up_ratio_pct": total_up_ratio,
            "avg_psr": total_avg_psr,
            "vs_kospi": "outperform" if (total_avg_chg is not None and kospi_chg is not None and total_avg_chg >= kospi_chg) else "underperform",
            "vs_nasdaq": "outperform" if (total_avg_chg is not None and nasdaq_chg is not None and total_avg_chg >= nasdaq_chg) else "underperform",
        }

        return {
            "start_date": sd,
            "end_date": ed,
            "kospi_change_pct": kospi_chg,
            "nasdaq_change_pct": nasdaq_chg,
            "summary": summary_row,
            "rows": rows,
        }
    finally:
        conn.close()


@router.get("/semiconductor/financials")
def get_semiconductor_financials(
    type: str = Query("annual", pattern="^(annual|quarterly)$")
):
    """
    SemiconductorView 종목실적 탭용 API.
    반환 형식:
      [{ name, lv1, type: 'revenue'|'profit', data: {period_label: value(억원)} }]
    """
    conn = _db()
    try:
        base_rows = conn.execute("""
            SELECT stock_code, company_name, lv1
            FROM semiconductor_valuestream
            WHERE stock_code IS NOT NULL AND stock_code != ''
            ORDER BY sort_order
        """).fetchall()
        if not base_rows:
            return []

        codes = [r["stock_code"] for r in base_rows]
        ph = ",".join("?" * len(codes))
        lv1_map = {r["stock_code"]: (r["lv1"] or "기타") for r in base_rows}
        name_map = {r["stock_code"]: r["company_name"] for r in base_rows}

        is_annual = 1 if type == "annual" else 0
        rows = conn.execute(
            f"""
            SELECT stock_code, year, quarter, revenue, operating_profit
            FROM financial_data
            WHERE stock_code IN ({ph})
              AND is_annual = ?
            ORDER BY stock_code, year ASC, quarter ASC
            """,
            codes + [is_annual],
        ).fetchall()

        data_map: Dict[str, Dict[str, Dict[str, float]]] = {}
        for r in rows:
            sc = r["stock_code"]
            if sc not in data_map:
                data_map[sc] = {"revenue": {}, "profit": {}}
            if type == "annual":
                period = f"{int(r['year'])}"
            else:
                q = int(r["quarter"] or 0)
                if q <= 0:
                    continue
                period = f"{int(r['year'])}.{q}Q"

            rev = r["revenue"]
            op = r["operating_profit"]
            data_map[sc]["revenue"][period] = round(rev / 1e8) if rev is not None else None
            data_map[sc]["profit"][period] = round(op / 1e8) if op is not None else None

        out: List[Dict[str, Any]] = []
        for sc in codes:
            if sc not in data_map:
                continue
            out.append({
                "name": name_map.get(sc, sc),
                "lv1": lv1_map.get(sc, "기타"),
                "type": "revenue",
                "data": data_map[sc]["revenue"],
            })
            out.append({
                "name": name_map.get(sc, sc),
                "lv1": lv1_map.get(sc, "기타"),
                "type": "profit",
                "data": data_map[sc]["profit"],
            })
        return out
    finally:
        conn.close()


@router.get("/semiconductor/financial-detail")
def get_semiconductor_financial_detail(stock_code: str = Query(...)):
    """
    종목실적 탭 하단 상세표용:
    - 연결/별도 재무상태 + 손익 (최근 연간)
    - 연결/별도 현금흐름 (최근 연간)
    """
    conn = _db()
    try:
        sc = (stock_code or "").strip()
        if not sc:
            return {"ok": False, "reason": "stock_code required"}

        fin_rows = conn.execute(
            """
            SELECT report_type, year, quarter, is_annual,
                   revenue, operating_profit, net_income,
                   total_assets, total_liabilities, total_equity
            FROM financial_data
            WHERE stock_code=?
              AND is_annual=1
              AND report_type IN ('CFS','OFS')
            ORDER BY year DESC, quarter DESC
            """,
            (sc,),
        ).fetchall()

        cf_rows = conn.execute(
            """
            SELECT report_type, year, quarter, is_annual,
                   operating_cf, investing_cf, financing_cf, capex
            FROM cash_flow_data
            WHERE stock_code=?
              AND is_annual=1
              AND report_type IN ('CFS','OFS')
            ORDER BY year DESC, quarter DESC
            """,
            (sc,),
        ).fetchall()

        def _pick_latest(rows, rt: str):
            for r in rows:
                if (r["report_type"] or "CFS") == rt:
                    return r
            return None

        cfs_fin = _pick_latest(fin_rows, "CFS")
        ofs_fin = _pick_latest(fin_rows, "OFS")
        cfs_cf = _pick_latest(cf_rows, "CFS")
        ofs_cf = _pick_latest(cf_rows, "OFS")

        def _to_uk(v):
            return round(float(v) / 1e8) if v is not None else None

        def _map_fin(r):
            if not r:
                return None
            return {
                "year": r["year"],
                "revenue": _to_uk(r["revenue"]),
                "operating_profit": _to_uk(r["operating_profit"]),
                "net_income": _to_uk(r["net_income"]),
                "total_assets": _to_uk(r["total_assets"]),
                "total_liabilities": _to_uk(r["total_liabilities"]),
                "total_equity": _to_uk(r["total_equity"]),
            }

        def _map_cf(r):
            if not r:
                return None
            op = _to_uk(r["operating_cf"])
            inv = _to_uk(r["investing_cf"])
            fin = _to_uk(r["financing_cf"])
            capex = _to_uk(r["capex"])
            fcf = (op - capex) if (op is not None and capex is not None) else None
            return {
                "year": r["year"],
                "operating_cf": op,
                "investing_cf": inv,
                "financing_cf": fin,
                "capex": capex,
                "free_cf": fcf,
            }

        return {
            "ok": True,
            "stock_code": sc,
            "financial": {
                "cfs": _map_fin(cfs_fin),
                "ofs": _map_fin(ofs_fin),
            },
            "cashflow": {
                "cfs": _map_cf(cfs_cf),
                "ofs": _map_cf(ofs_cf),
            },
        }
    finally:
        conn.close()
