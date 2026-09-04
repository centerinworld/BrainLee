"""
글로벌 경제 인텔리전스 라우터
세계 주요 경제지표를 수집·조회하는 API
"""
from fastapi import APIRouter, Query, BackgroundTasks
from typing import Optional
import sqlite3 as _sl
import json, time, logging, asyncio
from datetime import datetime, timedelta
from bisect import bisect_right
from config import IS_POSTGRES

router = APIRouter()
logger = logging.getLogger(__name__)
DB_PATH = "stock.db"

_init_done = False


def _init_tables():
    global _init_done
    if _init_done:
        return
    if IS_POSTGRES:
        _seed_categories()
        _init_done = True
        return
    conn = _sl.connect(DB_PATH, timeout=30)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS global_macro_categories (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            code         TEXT UNIQUE NOT NULL,
            name         TEXT NOT NULL,
            name_en      TEXT,
            category     TEXT NOT NULL,
            subcategory  TEXT,
            unit         TEXT,
            source       TEXT,
            source_code  TEXT,
            frequency    TEXT DEFAULT 'MONTHLY',
            description  TEXT,
            importance   INTEGER DEFAULT 1,
            is_active    INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS global_macro_data (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            indicator_code TEXT NOT NULL,
            date           TEXT NOT NULL,
            value          REAL,
            prev_value     REAL,
            change_pct     REAL,
            created_at     TEXT DEFAULT (datetime('now')),
            UNIQUE(indicator_code, date)
        );
        CREATE INDEX IF NOT EXISTS idx_gmd_code_date ON global_macro_data(indicator_code, date DESC);
        CREATE TABLE IF NOT EXISTS global_macro_events (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            event_date     TEXT NOT NULL,
            event_time     TEXT,
            country        TEXT,
            indicator_code TEXT,
            event_name     TEXT NOT NULL,
            importance     INTEGER DEFAULT 1,
            forecast       REAL,
            previous       REAL,
            actual         REAL,
            unit           TEXT,
            created_at     TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_gme_date ON global_macro_events(event_date);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_gme_unique_event
            ON global_macro_events(event_date, COALESCE(country,''), COALESCE(indicator_code,''), event_name);
        CREATE TABLE IF NOT EXISTS global_macro_collection_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            source       TEXT,
            status       TEXT,
            records      INTEGER DEFAULT 0,
            message      TEXT,
            run_at       TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS global_macro_event_reactions (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id       INTEGER NOT NULL,
            event_date     TEXT NOT NULL,
            country        TEXT,
            indicator_code TEXT,
            event_name     TEXT,
            surprise_value REAL,
            surprise_pct   REAL,
            asset_code     TEXT NOT NULL,
            asset_name     TEXT,
            asset_group    TEXT,
            "window"       TEXT NOT NULL,
            base_date      TEXT,
            end_date       TEXT,
            base_close     REAL,
            end_close      REAL,
            return_pct     REAL,
            direction      TEXT,
            impact_score   REAL,
            created_at     TEXT DEFAULT (datetime('now')),
            UNIQUE(event_id, asset_code, "window")
        );
        CREATE INDEX IF NOT EXISTS idx_gmer_event ON global_macro_event_reactions(event_id);
        CREATE INDEX IF NOT EXISTS idx_gmer_asset ON global_macro_event_reactions(asset_code, "window");
    """)
    for ddl in [
        "ALTER TABLE global_macro_events ADD COLUMN surprise_value REAL",
        "ALTER TABLE global_macro_events ADD COLUMN surprise_pct REAL",
        "ALTER TABLE global_macro_events ADD COLUMN surprise_basis TEXT",
        "ALTER TABLE global_macro_events ADD COLUMN status TEXT DEFAULT 'scheduled'",
        "ALTER TABLE global_macro_events ADD COLUMN source TEXT",
        "ALTER TABLE global_macro_events ADD COLUMN updated_at TEXT",
    ]:
        try:
            conn.execute(ddl)
        except Exception as exc:
            # 2026-08-14: PostgreSQL은 "column ... already exists"를 반환하며
            # db_compat.py가 psycopg 원본 예외를 그대로 재전파 — _sl.OperationalError
            # (SQLite 전용)만 잡던 기존 코드는 PostgreSQL 라우팅 상태에서 매번 실패.
            msg = str(exc).lower()
            if "duplicate column" not in msg and "already exists" not in msg:
                raise
    conn.commit()
    conn.close()
    _seed_categories()
    _init_done = True


def _seed_categories():
    """지표 메타데이터 초기값 삽입"""
    cats = [
        # ── 한국 ──────────────────────────────────────────────────────
        ("KR_BASE_RATE",      "한국 기준금리",          "Base Interest Rate",           "KOREA","MONETARY",    "%",       "ECOS",       "722Y001/0101000", "MONTHLY",  3),
        ("KR_CPI",            "한국 소비자물가지수",     "Consumer Price Index",         "KOREA","INFLATION",   "지수",    "ECOS",       "901Y009/0",       "MONTHLY",  3),
        ("KR_GDP_GROWTH",     "한국 실질GDP 성장률",    "Real GDP Growth",              "KOREA","GROWTH",      "%",       "ECOS",       "200Y001",         "QUARTERLY",3),
        ("KR_UNEMPLOYMENT",   "한국 실업률",            "Unemployment Rate",            "KOREA","EMPLOYMENT",  "%",       "ECOS",       "901Y027",         "MONTHLY",  2),
        ("KR_TRADE_BALANCE",  "한국 무역수지",          "Trade Balance",                "KOREA","TRADE",       "억달러",  "ECOS",       "901Y021",         "MONTHLY",  2),
        ("KR_EXPORT",         "한국 수출",              "Exports",                      "KOREA","TRADE",       "억달러",  "ECOS",       "901Y021",         "MONTHLY",  2),
        ("KR_IMPORT",         "한국 수입",              "Imports",                      "KOREA","TRADE",       "억달러",  "ECOS",       "901Y021",         "MONTHLY",  2),
        ("KR_M2",             "한국 광의통화(M2)",       "M2 Money Supply",              "KOREA","MONETARY",   "조원",    "ECOS",       "101Y002",         "MONTHLY",  1),
        ("KR_USD_KRW",        "원/달러 환율",           "USD/KRW Exchange Rate",        "KOREA","FX",          "원",      "ECOS/YAHOO", "731Y001/0000001", "DAILY",    3),
        ("KR_KOSPI",          "KOSPI 지수",            "KOSPI Index",                  "KOREA","MARKET",      "포인트",  "YAHOO",      "^KS11",           "DAILY",    2),
        ("KR_CURRENT_ACCOUNT","한국 경상수지",          "Current Account Balance",      "KOREA","TRADE",       "억달러",  "ECOS",       "901Y067",         "MONTHLY",  2),
        ("KR_INDUSTRIAL_PROD","한국 산업생산지수",       "Industrial Production Index",  "KOREA","GROWTH",      "지수",    "KOSIS",      "DT_1G_CO001",     "MONTHLY",  2),
        ("KR_RETAIL_SALES",   "한국 소매판매",          "Retail Sales",                 "KOREA","CONSUMPTION", "전년비%", "KOSIS",      "DT_1C8016",       "MONTHLY",  1),
        ("KR_HOUSING_PRICE",  "한국 주택매매가격지수",   "Housing Price Index",          "KOREA","REALESTATE",  "지수",    "KOSIS",      "DT_40803_N0001",  "MONTHLY",  2),
        # ── 미국 ──────────────────────────────────────────────────────
        ("US_FED_RATE",       "미국 기준금리(FFR)",     "Federal Funds Rate",           "US",   "MONETARY",    "%",       "FRED",       "FEDFUNDS",        "MONTHLY",  3),
        ("US_CPI",            "미국 CPI(전년비)",       "CPI Year-over-Year",           "US",   "INFLATION",   "%",       "FRED",       "CPIAUCSL",        "MONTHLY",  3),
        ("US_CORE_CPI",       "미국 Core CPI(전년비)",  "Core CPI YoY",                 "US",   "INFLATION",   "%",       "FRED",       "CPILFESL",        "MONTHLY",  3),
        ("US_PCE",            "미국 PCE(전년비)",       "PCE Inflation YoY",            "US",   "INFLATION",   "%",       "FRED",       "PCEPI",           "MONTHLY",  3),
        ("US_GDP_GROWTH",     "미국 실질GDP 성장률",    "Real GDP Growth QoQ Ann.",     "US",   "GROWTH",      "%",       "FRED",       "A191RL1Q225SBEA", "QUARTERLY",3),
        ("US_UNEMPLOYMENT",   "미국 실업률",            "Unemployment Rate",            "US",   "EMPLOYMENT",  "%",       "FRED",       "UNRATE",          "MONTHLY",  3),
        ("US_NONFARM",        "미국 비농업취업자수",     "Nonfarm Payrolls MoM",         "US",   "EMPLOYMENT",  "천명",    "FRED",       "PAYEMS",          "MONTHLY",  3),
        ("US_ISM_MFG",        "미국 ISM 제조업 PMI",    "ISM Manufacturing PMI",        "US",   "SENTIMENT",   "지수",    "FRED",       "MANEMP",          "MONTHLY",  2),
        ("US_RETAIL_SALES",   "미국 소매판매(MoM)",     "Retail Sales MoM",             "US",   "CONSUMPTION", "%",       "FRED",       "RSAFS",           "MONTHLY",  2),
        ("US_CONSUMER_CONF",  "미국 소비자신뢰지수",    "Consumer Confidence",          "US",   "SENTIMENT",   "지수",    "FRED",       "UMCSENT",         "MONTHLY",  2),
        ("US_10Y_YIELD",      "미국 10년 국채수익률",   "10Y Treasury Yield",           "US",   "BOND",        "%",       "FRED",       "GS10",            "MONTHLY",  3),
        ("US_2Y_YIELD",       "미국 2년 국채수익률",    "2Y Treasury Yield",            "US",   "BOND",        "%",       "FRED",       "GS2",             "MONTHLY",  2),
        ("US_DXY",            "달러인덱스(DXY)",        "US Dollar Index",              "US",   "FX",          "지수",    "YAHOO",      "DX-Y.NYB",        "DAILY",    3),
        ("US_M2",             "미국 M2",               "US M2 Money Supply",           "US",   "MONETARY",    "십억달러","FRED",        "M2SL",            "MONTHLY",  2),
        ("US_SP500",          "S&P 500",               "S&P 500 Index",                "US",   "MARKET",      "포인트",  "YAHOO",      "^GSPC",           "DAILY",    2),
        ("US_VIX",            "VIX 공포지수",           "CBOE VIX",                     "US",   "MARKET",      "지수",    "YAHOO",      "^VIX",            "DAILY",    2),
        ("US_10Y_YIELD_YH",   "미국 10년 국채수익률(Yahoo)", "10Y Treasury Yield (Yahoo)", "US",   "BOND",        "%",       "YAHOO",      "^TNX",            "DAILY",    2),
        ("US_HOUSING_START",  "미국 주택착공건수",       "Housing Starts",               "US",   "REALESTATE",  "천건",    "FRED",       "HOUST",           "MONTHLY",  2),
        ("US_CLI_OECD",       "미국 OECD 경기선행지수",  "US OECD CLI",                  "US",   "SENTIMENT",   "지수",    "OECD",       "DF_CLI/USA",      "MONTHLY",  2),
        ("US_GDP_GROWTH_WEO", "미국 IMF GDP 전망치",     "US IMF GDP Forecast",          "US",   "GROWTH",      "%",       "IMF",        "NGDP_RPCH/USA",   "ANNUAL",   2),
        # ── 유럽 ──────────────────────────────────────────────────────
        ("EU_ECB_RATE",       "유럽 ECB 기준금리",      "ECB Main Refinancing Rate",    "EU",   "MONETARY",    "%",       "ECB",        "",                "MONTHLY",  3),
        ("EU_CPI",            "유럽 CPI(전년비)",       "Euro Area CPI YoY",            "EU",   "INFLATION",   "%",       "WORLD_BANK", "FP.CPI.TOTL.ZG",  "ANNUAL",   2),
        ("EU_GDP_GROWTH",     "유럽 실질GDP 성장률",    "Euro Area Real GDP Growth",    "EU",   "GROWTH",      "%",       "WORLD_BANK", "NY.GDP.MKTP.KD.ZG","ANNUAL",  2),
        ("EU_UNEMPLOYMENT",   "유럽 실업률",            "Euro Area Unemployment",       "EU",   "EMPLOYMENT",  "%",       "WORLD_BANK", "SL.UEM.TOTL.ZS",  "ANNUAL",   2),
        ("EU_PMI_MFG",        "유럽 제조업 PMI",        "Euro Area Manufacturing PMI",  "EU",   "SENTIMENT",   "지수",    "MANUAL",     "",                "MONTHLY",  2),
        ("EU_EUR_USD",        "유로/달러 환율",         "EUR/USD Exchange Rate",        "EU",   "FX",          "달러",    "YAHOO",      "EURUSD=X",        "DAILY",    2),
        ("EU_DAX",            "독일 DAX 지수",          "DAX Index",                    "EU",   "MARKET",      "포인트",  "YAHOO",      "^GDAXI",          "DAILY",    2),
        ("EU_FTSE",           "영국 FTSE 100 지수",     "FTSE 100 Index",               "EU",   "MARKET",      "포인트",  "YAHOO",      "^FTSE",           "DAILY",    2),
        ("EU_GDP_GROWTH_WEO", "유럽 IMF GDP 전망치",     "EU IMF GDP Forecast",          "EU",   "GROWTH",      "%",       "IMF",        "NGDP_RPCH/EUQ",   "ANNUAL",   2),
        # ── 중국 ──────────────────────────────────────────────────────
        ("CN_GDP_GROWTH",     "중국 실질GDP 성장률",    "China Real GDP Growth",        "CN",   "GROWTH",      "%",       "WORLD_BANK", "NY.GDP.MKTP.KD.ZG","ANNUAL",  3),
        ("CN_CPI",            "중국 CPI(전년비)",       "China CPI YoY",                "CN",   "INFLATION",   "%",       "WORLD_BANK", "FP.CPI.TOTL.ZG",  "ANNUAL",   2),
        ("CN_PMI_MFG",        "중국 제조업 PMI(NBS)",   "China NBS Manufacturing PMI",  "CN",   "SENTIMENT",   "지수",    "MANUAL",     "",                "MONTHLY",  3),
        ("CN_EXPORT",         "중국 수출(전년비)",       "China Exports YoY",            "CN",   "TRADE",       "%",       "MANUAL",     "",                "MONTHLY",  2),
        ("CN_USD_CNY",        "위안/달러 환율",         "USD/CNY Exchange Rate",        "CN",   "FX",          "위안",    "YAHOO",      "USDCNY=X",        "DAILY",    2),
        ("CN_CLI_OECD",       "중국 OECD 경기선행지수",  "China OECD CLI",               "CN",   "SENTIMENT",   "지수",    "OECD",       "DF_CLI/CHN",      "MONTHLY",  2),
        ("CN_GDP_GROWTH_WEO", "중국 IMF GDP 전망치",     "China IMF GDP Forecast",       "CN",   "GROWTH",      "%",       "IMF",        "NGDP_RPCH/CHN",   "ANNUAL",   2),
        # ── 일본 ──────────────────────────────────────────────────────
        ("JP_BOJ_RATE",       "일본 BOJ 기준금리",      "BOJ Policy Rate",              "JP",   "MONETARY",    "%",       "MANUAL",     "",                "MONTHLY",  2),
        ("JP_CPI",            "일본 CPI(전년비)",       "Japan CPI YoY",                "JP",   "INFLATION",   "%",       "WORLD_BANK", "FP.CPI.TOTL.ZG",  "ANNUAL",   2),
        ("JP_USD_JPY",        "엔/달러 환율",           "USD/JPY Exchange Rate",        "JP",   "FX",          "엔",      "YAHOO",      "JPY=X",           "DAILY",    2),
        ("JP_NIKKEI",         "일본 닛케이 225 지수",    "Nikkei 225 Index",             "JP",   "MARKET",      "포인트",  "YAHOO",      "^N225",           "DAILY",    2),
        ("JP_CLI_OECD",       "일본 OECD 경기선행지수",  "Japan OECD CLI",               "JP",   "SENTIMENT",   "지수",    "OECD",       "DF_CLI/JPN",      "MONTHLY",  2),
        ("JP_GDP_GROWTH_WEO", "일본 IMF GDP 전망치",     "Japan IMF GDP Forecast",       "JP",   "GROWTH",      "%",       "IMF",        "NGDP_RPCH/JPN",   "ANNUAL",   2),
        # ── 원자재 ────────────────────────────────────────────────────
        ("COMM_OIL_WTI",      "WTI 원유 가격",          "WTI Crude Oil Price",          "COMMODITY","ENERGY", "달러/배럴","YAHOO",     "CL=F",            "DAILY",    3),
        ("COMM_OIL_BRENT",    "브렌트유 가격",          "Brent Crude Oil Price",        "COMMODITY","ENERGY", "달러/배럴","YAHOO",     "BZ=F",            "DAILY",    2),
        ("COMM_GOLD",         "금 가격",               "Gold Price",                   "COMMODITY","METAL",  "달러/온스","YAHOO",     "GC=F",            "DAILY",    3),
        ("COMM_COPPER",       "구리 가격",              "Copper Price",                 "COMMODITY","METAL",  "달러/파운드","YAHOO",   "HG=F",            "DAILY",    2),
        ("COMM_NATURAL_GAS",  "천연가스 가격",          "Natural Gas Price",            "COMMODITY","ENERGY", "달러/MMBtu","YAHOO",   "NG=F",            "DAILY",    2),
        ("COMM_WHEAT",        "소맥 가격",              "Wheat Futures Price",          "COMMODITY","AGRI",   "센트/부셸","YAHOO",    "ZW=F",            "DAILY",    2),
        ("OIL_STOCKS_EX_SPR", "미국 원유재고(SPR 제외)", "US Crude Stocks excl. SPR",    "COMMODITY","ENERGY", "천배럴",  "EIA",        "WCESTUS1",        "WEEKLY",   2),
        ("OIL_STOCKS_TOTAL",  "미국 원유재고(총계)",     "US Crude Stocks Total",        "COMMODITY","ENERGY", "천배럴",  "EIA",        "WCRSTUS1",        "WEEKLY",   1),
        # ── 글로벌 ────────────────────────────────────────────────────
        ("GLOBAL_TRADE_VOL",  "세계 무역량 증가율",     "World Trade Volume Growth",    "GLOBAL","TRADE",    "%",       "WORLD_BANK", "TM.VAL.MRCH.WL.CD","ANNUAL",  2),
        ("GLOBAL_GDP_GROWTH", "세계 실질GDP 성장률",    "World Real GDP Growth",        "GLOBAL","GROWTH",   "%",       "WORLD_BANK", "NY.GDP.MKTP.KD.ZG","ANNUAL",  3),
        ("GLOBAL_INFLATION",  "세계 평균 인플레이션",   "World Average Inflation",      "GLOBAL","INFLATION","%",       "WORLD_BANK", "FP.CPI.TOTL.ZG",  "ANNUAL",   2),
        ("GLOBAL_FOOD_PRICE", "FAO 식품가격지수",       "FAO Food Price Index",         "GLOBAL","FOOD",     "지수",    "FAO",        "",                "MONTHLY",  2),
        ("GLOBAL_FOOD_MEAT",  "FAO 육류가격지수",       "FAO Meat Price Index",         "GLOBAL","FOOD",     "지수",    "FAO",        "",                "MONTHLY",  1),
        ("GLOBAL_FOOD_DAIRY", "FAO 유제품가격지수",     "FAO Dairy Price Index",        "GLOBAL","FOOD",     "지수",    "FAO",        "",                "MONTHLY",  1),
        ("GLOBAL_FOOD_CEREALS","FAO 곡물가격지수",      "FAO Cereals Price Index",      "GLOBAL","FOOD",     "지수",    "FAO",        "",                "MONTHLY",  1),
        ("GLOBAL_FOOD_OILS",  "FAO 유지류가격지수",     "FAO Oils Price Index",         "GLOBAL","FOOD",     "지수",    "FAO",        "",                "MONTHLY",  1),
        ("GLOBAL_FOOD_SUGAR", "FAO 설탕가격지수",       "FAO Sugar Price Index",        "GLOBAL","FOOD",     "지수",    "FAO",        "",                "MONTHLY",  1),
    ]
    conn = _sl.connect(DB_PATH, timeout=30)
    conn.executemany("""
        INSERT INTO global_macro_categories
        (code,name,name_en,category,subcategory,unit,source,source_code,frequency,importance)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(code) DO UPDATE SET
            name=excluded.name,
            name_en=excluded.name_en,
            category=excluded.category,
            subcategory=excluded.subcategory,
            unit=excluded.unit,
            source=excluded.source,
            source_code=excluded.source_code,
            frequency=excluded.frequency,
            importance=excluded.importance
    """, cats)
    conn.commit()
    conn.close()


_init_tables()

# ── 캐시 ──────────────────────────────────────────────────────────────────────
_cache: dict = {}
_CACHE_TTL = 600  # 10분
_KOREA_FOCUS_CODES = [
    "KR_BASE_RATE",
    "KR_CPI",
    "KR_GDP_GROWTH",
    "KR_INDUSTRIAL_PROD",
    "KR_RETAIL_SALES",
    "KR_EMPLOYMENT",
    "KR_HOUSING_PRICE",
    "KR_USD_KRW",
]
_US_FOCUS_CODES = [
    "US_FED_RATE",
    "US_CPI",
    "US_GDP_GROWTH",
    "US_UNEMPLOYMENT",
    "US_RETAIL_SALES",
    "US_10Y_YIELD",
    "US_2Y_YIELD",
    "US_HOUSING_START",
]
_EU_FOCUS_CODES = [
    "EU_GDP_GROWTH",
    "EU_GDP_GROWTH_WEO",
    "EU_UNEMPLOYMENT",
    "EU_EUR_USD",
    "EU_DAX",
    "EU_FTSE",
]
_CN_FOCUS_CODES = [
    "CN_GDP_GROWTH",
    "CN_GDP_GROWTH_WEO",
    "CN_CLI_OECD",
    "CN_CPI",
    "CN_EXPORT",
    "CN_USD_CNY",
]
_JP_FOCUS_CODES = [
    "JP_CPI",
    "JP_GDP_GROWTH_WEO",
    "JP_CLI_OECD",
    "JP_BOJ_RATE",
    "JP_USD_JPY",
    "JP_NIKKEI",
]
_WEEK2_ECOS_CODES = [
    "KR_BASE_RATE",
    "KR_CPI",
    "KR_GDP_GROWTH",
    "KR_M2",
    "KR_TRADE_BALANCE",
    "KR_CURRENT_ACCOUNT",
]
_WEEK2_KOSIS_CODES = [
    "KR_INDUSTRIAL_PROD",
    "KR_RETAIL_SALES",
    "KR_EMPLOYMENT",
]
_WEEK3_FRED_CODES = [
    "US_FED_RATE",
    "US_CPI",
    "US_GDP_GROWTH",
    "US_UNEMPLOYMENT",
    "US_RETAIL_SALES",
    "US_HOUSING_START",
    "US_10Y_YIELD",
    "US_2Y_YIELD",
]
_WEEK4_OECD_CODES = [
    "US_CLI_OECD",
    "CN_CLI_OECD",
    "JP_CLI_OECD",
]
_WEEK4_IMF_CODES = [
    "US_GDP_GROWTH_WEO",
    "EU_GDP_GROWTH_WEO",
    "CN_GDP_GROWTH_WEO",
    "JP_GDP_GROWTH_WEO",
]
_WEEK4_REGION_CODES = {
    "eu": ["EU_GDP_GROWTH", "EU_UNEMPLOYMENT", "EU_EUR_USD", "EU_DAX", "EU_GDP_GROWTH_WEO"],
    "cn": ["CN_GDP_GROWTH", "CN_CPI", "CN_USD_CNY", "CN_CLI_OECD", "CN_GDP_GROWTH_WEO"],
    "jp": ["JP_CPI", "JP_USD_JPY", "JP_NIKKEI", "JP_CLI_OECD", "JP_GDP_GROWTH_WEO"],
}
_WEEK6_COMMODITY_CODES = [
    "COMM_OIL_WTI",
    "COMM_OIL_BRENT",
    "COMM_GOLD",
    "COMM_COPPER",
    "COMM_NATURAL_GAS",
    "COMM_WHEAT",
]
_WEEK6_FX_CODES = [
    "KR_USD_KRW",
    "JP_USD_JPY",
    "CN_USD_CNY",
    "EU_EUR_USD",
    "US_DXY",
]
_WEEK6_OIL_SUPPLY_CODES = [
    "OIL_STOCKS_EX_SPR",
    "OIL_STOCKS_TOTAL",
]
_WEEK6_FOOD_CODES = [
    "GLOBAL_FOOD_PRICE",
    "GLOBAL_FOOD_MEAT",
    "GLOBAL_FOOD_DAIRY",
    "GLOBAL_FOOD_CEREALS",
    "GLOBAL_FOOD_OILS",
    "GLOBAL_FOOD_SUGAR",
]


def _cached(key: str, fn):
    entry = _cache.get(key)
    if entry and time.time() - entry["ts"] < _CACHE_TTL:
        return entry["data"]
    data = fn()
    _cache[key] = {"data": data, "ts": time.time()}
    return data


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────
def _conn():
    c = _sl.connect(DB_PATH, timeout=30)
    c.row_factory = _sl.Row
    return c


def _pct_change(cur, prev):
    if cur is None or prev in (None, 0):
        return None
    try:
        return ((float(cur) - float(prev)) / abs(float(prev))) * 100.0
    except Exception:
        return None


def _change_basis(freq: str | None) -> str:
    freq = (freq or "").upper()
    if freq == "DAILY":
        return "전일"
    if freq == "WEEKLY":
        return "전주"
    if freq == "MONTHLY":
        return "전월"
    if freq == "QUARTERLY":
        return "전분기"
    if freq == "ANNUAL":
        return "전년"
    return "전기"


def _parse_dt(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except Exception:
        return None


def _build_series_metrics(rows: list[dict], frequency: str | None) -> list[dict]:
    points = [
        {
            "date": r["date"],
            "value": r["value"],
        }
        for r in rows
        if r.get("date") and r.get("value") is not None
    ]
    if not points:
        return []

    freq = (frequency or "").upper()
    dates = [p["date"] for p in points]
    parsed_dates = [_parse_dt(p["date"]) for p in points]
    yoy_steps = {"MONTHLY": 12, "QUARTERLY": 4, "ANNUAL": 1}.get(freq)

    enriched = []
    for idx, point in enumerate(points):
        cur = point["value"]
        prev = points[idx - 1]["value"] if idx > 0 else None
        change_pct = _pct_change(cur, prev)
        change_value = (cur - prev) if prev is not None else None

        yoy_prev = None
        if yoy_steps and idx >= yoy_steps:
            yoy_prev = points[idx - yoy_steps]["value"]
        elif freq == "DAILY" and parsed_dates[idx]:
            target = parsed_dates[idx] - timedelta(days=365)
            target_str = target.strftime("%Y-%m-%d")
            pos = bisect_right(dates, target_str) - 1
            if pos >= 0:
                yoy_prev = points[pos]["value"]

        item = {
            "date": point["date"],
            "value": cur,
            "prev_value": prev,
            "change_value": change_value,
            "change_pct": change_pct,
            "change_basis": _change_basis(freq),
            "mom_value": prev if freq == "MONTHLY" else None,
            "mom_change_pct": change_pct if freq == "MONTHLY" else None,
            "yoy_value": yoy_prev,
            "yoy_change_pct": _pct_change(cur, yoy_prev),
        }
        enriched.append(item)
    return enriched


def _load_series_map(conn, codes: list[str]) -> dict[str, list[dict]]:
    wanted = sorted({c for c in codes if c})
    if not wanted:
        return {}
    placeholders = ",".join("?" for _ in wanted)
    rows = conn.execute(f"""
        SELECT indicator_code, date, value
        FROM global_macro_data
        WHERE indicator_code IN ({placeholders}) AND value IS NOT NULL
        ORDER BY indicator_code, date
    """, wanted).fetchall()
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["indicator_code"], []).append({
            "date": row["date"],
            "value": row["value"],
        })
    return grouped


def _enrich_latest_rows(conn, rows: list[dict]) -> list[dict]:
    series_map = _load_series_map(conn, [r.get("code") for r in rows])
    enriched = []
    for row in rows:
        item = dict(row)
        series = series_map.get(item.get("code"), [])
        metrics = _build_series_metrics(series, item.get("frequency"))
        latest = metrics[-1] if metrics else {}
        if latest:
            item.update(latest)
        enriched.append(item)
    return enriched


def _week2_progress(conn):
    def _count_ready(codes: list[str]) -> int:
        placeholders = ",".join("?" for _ in codes)
        return conn.execute(f"""
            SELECT COUNT(DISTINCT indicator_code)
            FROM global_macro_data
            WHERE value IS NOT NULL AND indicator_code IN ({placeholders})
        """, codes).fetchone()[0]

    ecos_ready = _count_ready(_WEEK2_ECOS_CODES)
    kosis_ready = _count_ready(_WEEK2_KOSIS_CODES)
    housing_ready = _count_ready(["KR_HOUSING_PRICE"]) > 0
    done_flags = [
        ecos_ready == len(_WEEK2_ECOS_CODES),
        kosis_ready == len(_WEEK2_KOSIS_CODES),
        housing_ready,
        True,
        True,
    ]
    done_count = sum(1 for x in done_flags if x)
    if done_count == len(done_flags):
        status = "done"
    elif done_count > 0:
        status = "in_progress"
    else:
        status = "planned"
    return {
        "status": status,
        "done_count": done_count,
        "total_count": len(done_flags),
        "ecos_ready": ecos_ready,
        "ecos_total": len(_WEEK2_ECOS_CODES),
        "kosis_ready": kosis_ready,
        "kosis_total": len(_WEEK2_KOSIS_CODES),
        "housing_ready": housing_ready,
    }


def _latest_code_map(conn, codes: list[str]) -> dict[str, dict]:
    placeholders = ",".join("?" for _ in codes)
    rows = conn.execute(f"""
        SELECT c.code, c.name, c.category, c.subcategory,
               c.name_en, c.unit, c.source, c.frequency, c.importance,
               d.date, d.value, d.prev_value, d.change_pct
        FROM global_macro_categories c
        LEFT JOIN global_macro_data d ON d.indicator_code = c.code
            AND d.date = (
                SELECT MAX(date) FROM global_macro_data
                WHERE indicator_code = c.code AND value IS NOT NULL
            )
        WHERE c.code IN ({placeholders})
    """, codes).fetchall()
    enriched = _enrich_latest_rows(conn, [dict(r) for r in rows])
    return {item["code"]: item for item in enriched}


def _week3_progress(conn):
    placeholders = ",".join("?" for _ in _WEEK3_FRED_CODES)
    fred_ready = conn.execute(f"""
        SELECT COUNT(DISTINCT indicator_code)
        FROM global_macro_data
        WHERE value IS NOT NULL AND indicator_code IN ({placeholders})
    """, _WEEK3_FRED_CODES).fetchone()[0]

    latest_map = _latest_code_map(conn, ["US_2Y_YIELD", "US_10Y_YIELD", "US_10Y_YIELD_YH"])
    has_10y = latest_map.get("US_10Y_YIELD", {}).get("value") is not None or latest_map.get("US_10Y_YIELD_YH", {}).get("value") is not None
    has_2y = latest_map.get("US_2Y_YIELD", {}).get("value") is not None
    yield_curve_ready = has_10y
    spread_ready = has_10y and has_2y
    fomc_ready = conn.execute("""
        SELECT COUNT(*)
        FROM global_macro_events
        WHERE country = 'US'
          AND source = 'official_fed_fomc'
          AND event_name LIKE '%FOMC%'
    """).fetchone()[0] > 0
    done_flags = [
        fred_ready == len(_WEEK3_FRED_CODES),
        yield_curve_ready,
        fomc_ready,
        True,
        spread_ready,
    ]
    done_count = sum(1 for x in done_flags if x)
    if done_count == len(done_flags):
        status = "done"
    elif done_count > 0:
        status = "in_progress"
    else:
        status = "planned"
    return {
        "status": status,
        "done_count": done_count,
        "total_count": len(done_flags),
        "fred_ready": fred_ready,
        "fred_total": len(_WEEK3_FRED_CODES),
        "yield_curve_ready": yield_curve_ready,
        "fomc_ready": fomc_ready,
        "spread_ready": spread_ready,
    }


def _build_us_signal(conn) -> dict:
    latest_map = _latest_code_map(conn, ["US_2Y_YIELD", "US_10Y_YIELD", "US_10Y_YIELD_YH"])
    ten = latest_map.get("US_10Y_YIELD") or latest_map.get("US_10Y_YIELD_YH") or {}
    two = latest_map.get("US_2Y_YIELD") or {}
    ten_val = ten.get("value")
    two_val = two.get("value")
    spread = None
    if ten_val is not None and two_val is not None:
        spread = round(float(ten_val) - float(two_val), 3)
    signal = "unavailable"
    summary = "2Y 또는 10Y 데이터가 부족합니다."
    if spread is not None:
        if spread < 0:
            signal = "inversion"
            summary = "장단기 금리차 역전 구간입니다."
        elif spread < 0.5:
            signal = "flat"
            summary = "장단기 금리차가 평탄화되었습니다."
        else:
            signal = "normal"
            summary = "장단기 금리차가 정상 범위입니다."
    curve = []
    if two.get("value") is not None:
        curve.append({"tenor": "2Y", "value": two.get("value"), "date": two.get("date")})
    if ten.get("value") is not None:
        curve.append({"tenor": "10Y", "value": ten.get("value"), "date": ten.get("date")})
    return {
        "yield_curve": curve,
        "spread_2s10s": spread,
        "signal": signal,
        "summary": summary,
        "as_of": ten.get("date") or two.get("date"),
    }


def _week4_progress(conn):
    ready = {}
    for key, codes in _WEEK4_REGION_CODES.items():
        placeholders = ",".join("?" for _ in codes)
        ready[key] = conn.execute(f"""
            SELECT COUNT(DISTINCT indicator_code)
            FROM global_macro_data
            WHERE value IS NOT NULL AND indicator_code IN ({placeholders})
        """, codes).fetchone()[0]

    def _count_ready(codes: list[str]) -> int:
        placeholders = ",".join("?" for _ in codes)
        return conn.execute(f"""
            SELECT COUNT(DISTINCT indicator_code)
            FROM global_macro_data
            WHERE value IS NOT NULL AND indicator_code IN ({placeholders})
        """, codes).fetchone()[0]

    oecd_ready = _count_ready(_WEEK4_OECD_CODES)
    imf_ready = _count_ready(_WEEK4_IMF_CODES)
    oecd_done = oecd_ready == len(_WEEK4_OECD_CODES)
    imf_done = imf_ready == len(_WEEK4_IMF_CODES)
    eu_done = ready["eu"] >= 3
    cn_done = ready["cn"] >= 3
    jp_done = ready["jp"] >= 3
    done_flags = [
        oecd_done,
        imf_done,
        eu_done,
        cn_done,
        jp_done,
    ]
    done_count = sum(1 for x in done_flags if x)
    if done_count == len(done_flags):
        status = "done"
    elif done_count > 0:
        status = "in_progress"
    else:
        status = "planned"
    return {
        "status": status,
        "done_count": done_count,
        "total_count": len(done_flags),
        "oecd_ready": oecd_ready,
        "oecd_total": len(_WEEK4_OECD_CODES),
        "imf_ready": imf_ready,
        "imf_total": len(_WEEK4_IMF_CODES),
        "eu_ready": ready["eu"],
        "eu_total": len(_WEEK4_REGION_CODES["eu"]),
        "cn_ready": ready["cn"],
        "cn_total": len(_WEEK4_REGION_CODES["cn"]),
        "jp_ready": ready["jp"],
        "jp_total": len(_WEEK4_REGION_CODES["jp"]),
    }


def _week5_progress(conn):
    now = datetime.now()
    start = (now - timedelta(days=14)).strftime("%Y-%m-%d")
    end = (now + timedelta(days=45)).strftime("%Y-%m-%d")
    total_events = conn.execute("""
        SELECT COUNT(*) FROM global_macro_events
        WHERE event_date BETWEEN ? AND ?
    """, (start, end)).fetchone()[0]
    actual_events = conn.execute("""
        SELECT COUNT(*) FROM global_macro_events
        WHERE actual IS NOT NULL
    """).fetchone()[0]
    surprise_events = conn.execute("""
        SELECT COUNT(*) FROM global_macro_events
        WHERE (forecast IS NOT NULL AND actual IS NOT NULL)
           OR (previous IS NOT NULL AND actual IS NOT NULL)
    """).fetchone()[0]
    upcoming_events = conn.execute("""
        SELECT COUNT(*) FROM global_macro_events
        WHERE event_date >= ?
    """, (now.strftime("%Y-%m-%d"),)).fetchone()[0]
    reaction_links = conn.execute("""
        SELECT COUNT(*) FROM global_macro_event_reactions
    """).fetchone()[0]
    done_flags = [
        total_events > 0,
        True,
        surprise_events > 0,
        upcoming_events > 0,
        reaction_links > 0,
    ]
    done_count = sum(1 for x in done_flags if x)
    if done_count == len(done_flags):
        status = "done"
    elif done_count > 0:
        status = "in_progress"
    else:
        status = "planned"
    return {
        "status": status,
        "done_count": done_count,
        "total_count": len(done_flags),
        "events_window_count": total_events,
        "actual_events": actual_events,
        "surprise_events": surprise_events,
        "upcoming_events": upcoming_events,
        "reaction_links": reaction_links,
    }


def _count_ready_codes(conn, codes: list[str]) -> int:
    if not codes:
        return 0
    placeholders = ",".join("?" for _ in codes)
    return conn.execute(f"""
        SELECT COUNT(DISTINCT indicator_code)
        FROM global_macro_data
        WHERE value IS NOT NULL AND indicator_code IN ({placeholders})
    """, codes).fetchone()[0]


def _week6_progress(conn):
    commodity_ready = _count_ready_codes(conn, _WEEK6_COMMODITY_CODES)
    fx_ready = _count_ready_codes(conn, _WEEK6_FX_CODES)
    oil_supply_ready = _count_ready_codes(conn, _WEEK6_OIL_SUPPLY_CODES)
    food_ready = _count_ready_codes(conn, ["GLOBAL_FOOD_PRICE"])
    sector_rows = conn.execute("SELECT COUNT(*) FROM sector_index_daily WHERE close IS NOT NULL AND close > 0").fetchone()[0]
    correlation_ready = commodity_ready > 0 and sector_rows > 0
    done_flags = [
        commodity_ready >= 5,
        fx_ready >= 5,
        correlation_ready,
        oil_supply_ready >= 1,
        food_ready > 0,
    ]
    done_count = sum(1 for x in done_flags if x)
    if done_count == len(done_flags):
        status = "done"
    elif done_count > 0:
        status = "in_progress"
    else:
        status = "planned"
    return {
        "status": status,
        "done_count": done_count,
        "total_count": len(done_flags),
        "commodity_ready": commodity_ready,
        "commodity_total": len(_WEEK6_COMMODITY_CODES),
        "fx_ready": fx_ready,
        "fx_total": len(_WEEK6_FX_CODES),
        "correlation_ready": correlation_ready,
        "oil_supply_ready": oil_supply_ready,
        "oil_supply_total": len(_WEEK6_OIL_SUPPLY_CODES),
        "food_ready": food_ready > 0,
    }


def _build_week4_regions(conn) -> list[dict]:
    region_specs = [
        ("EU", "유럽", _EU_FOCUS_CODES),
        ("CN", "중국", _CN_FOCUS_CODES),
        ("JP", "일본", _JP_FOCUS_CODES),
    ]
    items = []
    for code, label, focus_codes in region_specs:
        latest_map = _latest_code_map(conn, focus_codes)
        available = [latest_map[c] for c in focus_codes if latest_map.get(c, {}).get("value") is not None]
        items.append({
            "code": code,
            "label": label,
            "available_count": len(available),
            "total_count": len(focus_codes),
            "highlights": available[:3],
        })
    return items


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 20 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den_x = sum((x - mx) ** 2 for x in xs) ** 0.5
    den_y = sum((y - my) ** 2 for y in ys) ** 0.5
    if not den_x or not den_y:
        return None
    return num / (den_x * den_y)


def _series_returns(rows: list[dict]) -> dict[str, float]:
    result = {}
    prev = None
    for row in rows:
        val = row.get("value")
        if val is not None and prev not in (None, 0):
            result[row["date"]] = ((float(val) / float(prev)) - 1.0) * 100.0
        if val is not None:
            prev = float(val)
    return result


def _sector_returns(rows: list[_sl.Row]) -> dict[str, float]:
    result = {}
    prev = None
    for row in rows:
        val = row["close"]
        if val is not None and prev not in (None, 0):
            result[row["date"]] = ((float(val) / float(prev)) - 1.0) * 100.0
        if val is not None:
            prev = float(val)
    return result


def _commodity_sector_correlations(conn, days: int = 365, limit: int = 20) -> list[dict]:
    days = max(days, 1095)
    sector_max = conn.execute("""
        SELECT MAX(date) FROM sector_index_daily
        WHERE close IS NOT NULL AND close > 0
    """).fetchone()[0]
    anchor = _parse_dt(sector_max) or datetime.now()
    since = (anchor - timedelta(days=days)).strftime("%Y-%m-%d")
    series_map = _load_series_map(conn, _WEEK6_COMMODITY_CODES)
    meta_map = _latest_code_map(conn, _WEEK6_COMMODITY_CODES)
    source_map = {
        row["code"]: row["source_code"]
        for row in conn.execute("""
            SELECT code, source_code
            FROM global_macro_categories
            WHERE code IN ({})
        """.format(",".join("?" for _ in _WEEK6_COMMODITY_CODES)), _WEEK6_COMMODITY_CODES).fetchall()
        if row["source_code"]
    }
    sector_rows = conn.execute("""
        SELECT market, sector, date, close
        FROM sector_index_daily
        WHERE date >= ? AND close IS NOT NULL AND close > 0
        ORDER BY market, sector, date
    """, (since,)).fetchall()
    grouped_sectors: dict[tuple[str, str], list[_sl.Row]] = {}
    for row in sector_rows:
        grouped_sectors.setdefault((row["market"], row["sector"]), []).append(row)

    rows = []
    for code in _WEEK6_COMMODITY_CODES:
        points = series_map.get(code, [])
        comm_returns = {}
        if source_map.get(code):
            price_rows = conn.execute("""
                SELECT date, close AS value
                FROM price_history
                WHERE stock_code = ?
                  AND date >= ?
                  AND close IS NOT NULL
                  AND close > 0
                ORDER BY date
            """, (source_map[code], since)).fetchall()
            comm_returns = _series_returns([dict(r) for r in price_rows])
        if len(comm_returns) < 20:
            comm_returns = _series_returns([p for p in points if p["date"] >= since])
        if len(comm_returns) < 20:
            continue
        for (market, sector), srows in grouped_sectors.items():
            sec_returns = _sector_returns(srows)
            dates = sorted(set(comm_returns) & set(sec_returns))
            if len(dates) < 20:
                continue
            corr = _pearson([comm_returns[d] for d in dates], [sec_returns[d] for d in dates])
            if corr is None:
                continue
            rows.append({
                "commodity_code": code,
                "commodity_name": meta_map.get(code, {}).get("name", code),
                "market": market,
                "sector": sector,
                "correlation": round(corr, 4),
                "abs_correlation": round(abs(corr), 4),
                "sample_size": len(dates),
                "start_date": dates[0],
                "end_date": dates[-1],
            })
    rows.sort(key=lambda r: r["abs_correlation"], reverse=True)
    return rows[:limit]


def _severity_from_abs(value: float, medium: float, high: float) -> str:
    value = abs(value)
    if value >= high:
        return "high"
    if value >= medium:
        return "medium"
    return "low"


def _recent_zscore(points: list[dict]) -> tuple[float | None, float | None]:
    values = [float(p["value"]) for p in points if p.get("value") is not None]
    if len(values) < 30:
        return None, None
    latest = values[-1]
    baseline = values[-252:] if len(values) >= 252 else values
    mean = sum(baseline) / len(baseline)
    variance = sum((v - mean) ** 2 for v in baseline) / len(baseline)
    stdev = variance ** 0.5
    if not stdev:
        return None, latest
    return (latest - mean) / stdev, latest


def _build_macro_insights(conn, limit: int = 30) -> dict:
    codes = sorted(set(
        _WEEK6_COMMODITY_CODES
        + _WEEK6_FX_CODES
        + _WEEK6_FOOD_CODES
        + _WEEK6_OIL_SUPPLY_CODES
        + ["US_SP500", "US_VIX", "KR_KOSPI", "US_10Y_YIELD", "US_2Y_YIELD", "US_10Y_YIELD_YH"]
    ))
    latest_map = _latest_code_map(conn, codes)
    series_map = _load_series_map(conn, codes)
    insights: list[dict] = []

    movers = []
    for code, item in latest_map.items():
        change = item.get("change_pct")
        if change is None:
            continue
        movers.append((abs(float(change)), code, item))
    for abs_change, code, item in sorted(movers, reverse=True)[:8]:
        change = float(item.get("change_pct") or 0)
        insights.append({
            "type": "mover",
            "severity": _severity_from_abs(change, 1.5, 4.0),
            "title": f"{item.get('name', code)} {change:+.2f}%",
            "summary": f"{item.get('date', '')} 기준 {item.get('name', code)}가 {_change_basis(item.get('frequency'))} 대비 {change:+.2f}% 변했습니다.",
            "code": code,
            "date": item.get("date"),
            "value": item.get("value"),
            "unit": item.get("unit"),
            "change_pct": change,
        })

    anomalies = []
    for code, points in series_map.items():
        z, latest = _recent_zscore(points)
        if z is None or abs(z) < 1.5:
            continue
        item = latest_map.get(code, {})
        anomalies.append((abs(z), code, z, latest, item))
    for _, code, z, latest, item in sorted(anomalies, reverse=True)[:8]:
        insights.append({
            "type": "anomaly",
            "severity": _severity_from_abs(z, 1.5, 2.5),
            "title": f"{item.get('name', code)} 분포 이탈 {z:+.1f}σ",
            "summary": f"최근 관측값이 최근 표본 평균 대비 {z:+.1f} 표준편차 위치입니다.",
            "code": code,
            "date": item.get("date"),
            "value": latest,
            "unit": item.get("unit"),
            "zscore": round(z, 2),
        })

    def _chg(code: str) -> float | None:
        val = latest_map.get(code, {}).get("change_pct")
        return float(val) if val is not None else None

    combo_specs = [
        (
            "inflation_pressure",
            "원자재·달러 동반 상승 압력",
            ["COMM_OIL_WTI", "COMM_COPPER", "US_DXY"],
            lambda vals: sum(v for v in vals if v and v > 0),
            2.0,
            "원유·구리·달러 중 여러 축이 동시에 상승하면 비용/수입물가 압력이 커질 수 있습니다.",
        ),
        (
            "risk_off",
            "안전자산/변동성 동반 신호",
            ["COMM_GOLD", "US_VIX", "KR_USD_KRW"],
            lambda vals: sum(v for v in vals if v and v > 0),
            2.0,
            "금·VIX·원달러가 함께 강하면 위험회피 흐름을 의심할 수 있습니다.",
        ),
        (
            "korea_pressure",
            "한국 증시 부담 조합",
            ["KR_USD_KRW", "US_DXY", "KR_KOSPI"],
            lambda vals: (vals[0] or 0) + (vals[1] or 0) - (vals[2] or 0),
            1.5,
            "원달러/달러인덱스 상승과 KOSPI 약세가 겹치면 국내 위험프리미엄 확대 신호입니다.",
        ),
    ]
    for insight_type, title, combo_codes, scorer, threshold, summary in combo_specs:
        vals = [_chg(code) for code in combo_codes]
        if any(v is None for v in vals):
            continue
        score = scorer(vals)
        if abs(score) < threshold:
            continue
        insights.append({
            "type": insight_type,
            "severity": _severity_from_abs(score, threshold, threshold * 2.5),
            "title": title,
            "summary": summary,
            "codes": combo_codes,
            "score": round(score, 2),
            "components": [
                {
                    "code": code,
                    "name": latest_map.get(code, {}).get("name", code),
                    "change_pct": _chg(code),
                }
                for code in combo_codes
            ],
        })

    reaction_rows = conn.execute("""
        SELECT event_date, event_name, asset_name, "window", return_pct, impact_score
        FROM global_macro_event_reactions
        ORDER BY ABS(COALESCE(impact_score, return_pct, 0)) DESC
        LIMIT 5
    """).fetchall()
    for row in reaction_rows:
        ret = float(row["return_pct"] or 0)
        insights.append({
            "type": "event_reaction",
            "severity": _severity_from_abs(row["impact_score"] or ret, 3.0, 8.0),
            "title": f"{row['event_name']} 이후 {row['asset_name']} {ret:+.2f}%",
            "summary": f"{row['event_date']} 발표 후 {row['window']} 기준 시장 반응입니다.",
            "date": row["event_date"],
            "return_pct": ret,
            "impact_score": row["impact_score"],
        })

    for row in _commodity_sector_correlations(conn, days=365, limit=5):
        corr = float(row["correlation"])
        insights.append({
            "type": "correlation",
            "severity": _severity_from_abs(corr, 0.08, 0.15),
            "title": f"{row['commodity_name']} ↔ {row['market']} {row['sector']}",
            "summary": f"최근 가용 표본 {row['sample_size']}개 기준 상관계수 {corr:+.2f}입니다.",
            "correlation": corr,
            "sample_size": row["sample_size"],
            "start_date": row["start_date"],
            "end_date": row["end_date"],
        })

    priority = {"high": 3, "medium": 2, "low": 1}
    insights.sort(key=lambda x: (priority.get(x.get("severity"), 0), abs(float(x.get("score") or x.get("zscore") or x.get("impact_score") or x.get("change_pct") or x.get("correlation") or 0))), reverse=True)
    return {
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": min(len(insights), limit),
        "insights": insights[:limit],
    }


def _series_latest_return(conn, code: str, periods: int = 20) -> dict:
    rows = conn.execute("""
        SELECT date, value
        FROM global_macro_data
        WHERE indicator_code = ? AND value IS NOT NULL
        ORDER BY date DESC
        LIMIT ?
    """, (code, periods + 1)).fetchall()
    points = [dict(r) for r in reversed(rows)]
    if len(points) < 2:
        return {"code": code, "return_pct": None}
    first = points[0]["value"]
    last = points[-1]["value"]
    return {
        "code": code,
        "start_date": points[0]["date"],
        "end_date": points[-1]["date"],
        "start_value": first,
        "end_value": last,
        "return_pct": _pct_change(last, first),
    }


def _build_market_regime(conn) -> dict:
    latest = _latest_code_map(conn, [
        "US_VIX", "US_DXY", "KR_USD_KRW", "KR_KOSPI",
        "US_SP500", "COMM_OIL_WTI", "COMM_GOLD", "US_10Y_YIELD",
        "US_2Y_YIELD", "US_10Y_YIELD_YH",
    ])
    series = _load_series_map(conn, list(latest.keys()))

    def zscore(code: str) -> float | None:
        z, _ = _recent_zscore(series.get(code, []))
        return z

    kospi_20d = _series_latest_return(conn, "KR_KOSPI", 20)
    sp500_20d = _series_latest_return(conn, "US_SP500", 20)
    oil_20d = _series_latest_return(conn, "COMM_OIL_WTI", 20)
    dxy_20d = _series_latest_return(conn, "US_DXY", 20)
    krw_20d = _series_latest_return(conn, "KR_USD_KRW", 20)
    gold_20d = _series_latest_return(conn, "COMM_GOLD", 20)

    ten = latest.get("US_10Y_YIELD") or latest.get("US_10Y_YIELD_YH") or {}
    two = latest.get("US_2Y_YIELD") or {}
    spread = None
    if ten.get("value") is not None and two.get("value") is not None:
        spread = float(ten["value"]) - float(two["value"])

    components = []

    def add_component(name: str, value: float | None, weight: float, risk_when: str, note: str):
        if value is None:
            return
        if risk_when == "positive":
            score = max(0.0, min(1.0, value))
        elif risk_when == "negative":
            score = max(0.0, min(1.0, -value))
        else:
            score = max(0.0, min(1.0, abs(value)))
        components.append({"name": name, "raw": round(value, 4), "weight": weight, "score": round(score, 4), "note": note})

    add_component("VIX 분포", zscore("US_VIX") / 2.5 if zscore("US_VIX") is not None else None, 20, "positive", "VIX가 평소보다 높을수록 위험회피 점수 상승")
    add_component("달러 강세", (dxy_20d["return_pct"] or 0) / 3.0 if dxy_20d["return_pct"] is not None else None, 15, "positive", "달러인덱스 20거래일 변화")
    add_component("원화 약세", (krw_20d["return_pct"] or 0) / 3.0 if krw_20d["return_pct"] is not None else None, 15, "positive", "원달러 20거래일 변화")
    add_component("KOSPI 모멘텀", (kospi_20d["return_pct"] or 0) / 5.0 if kospi_20d["return_pct"] is not None else None, 15, "negative", "KOSPI 20거래일 수익률 약세")
    add_component("S&P500 모멘텀", (sp500_20d["return_pct"] or 0) / 5.0 if sp500_20d["return_pct"] is not None else None, 10, "negative", "S&P500 20거래일 수익률 약세")
    add_component("유가 압력", (oil_20d["return_pct"] or 0) / 8.0 if oil_20d["return_pct"] is not None else None, 10, "positive", "WTI 20거래일 상승률")
    add_component("금 선호", (gold_20d["return_pct"] or 0) / 6.0 if gold_20d["return_pct"] is not None else None, 5, "positive", "금 20거래일 상승률")
    add_component("수익률곡선", (-(spread or 0) / 1.0) if spread is not None else None, 10, "positive", "2Y-10Y 역전 시 경기 리스크 점수 상승")

    total_weight = sum(c["weight"] for c in components) or 1
    risk_score = sum(c["score"] * c["weight"] for c in components) / total_weight * 100.0
    if risk_score >= 70:
        regime = "risk_off"
        label = "위험회피 우위"
    elif risk_score >= 45:
        regime = "caution"
        label = "중립·주의"
    else:
        regime = "risk_on"
        label = "위험선호 우위"

    watch = []
    for c in sorted(components, key=lambda x: x["score"] * x["weight"], reverse=True)[:4]:
        watch.append({"name": c["name"], "note": c["note"], "contribution": round(c["score"] * c["weight"], 2)})

    return {
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "regime": regime,
        "label": label,
        "risk_score": round(risk_score, 1),
        "spread_10y_2y": round(spread, 3) if spread is not None else None,
        "components": components,
        "watch": watch,
    }


def _lead_lag_correlations(conn, target_code: str = "KR_KOSPI", days: int = 730, limit: int = 12) -> list[dict]:
    codes = ["US_SP500", "US_VIX", "US_DXY", "KR_USD_KRW", "COMM_OIL_WTI", "COMM_GOLD", "COMM_COPPER", "US_10Y_YIELD_YH"]
    since = (datetime.now() - timedelta(days=max(days, 365))).strftime("%Y-%m-%d")
    series_map = _load_series_map(conn, [target_code] + codes)
    meta_map = _latest_code_map(conn, [target_code] + codes)
    target_returns = _series_returns([p for p in series_map.get(target_code, []) if p["date"] >= since])
    rows = []
    lags = [-20, -10, -5, 0, 5, 10, 20]
    for code in codes:
        source_returns = _series_returns([p for p in series_map.get(code, []) if p["date"] >= since])
        if len(source_returns) < 40 or len(target_returns) < 40:
            continue
        source_dates = sorted(source_returns)
        source_idx = {date: i for i, date in enumerate(source_dates)}
        best = None
        for lag in lags:
            xs, ys = [], []
            for date, target_ret in target_returns.items():
                idx = source_idx.get(date)
                if idx is None:
                    continue
                source_pos = idx - lag
                if source_pos < 0 or source_pos >= len(source_dates):
                    continue
                source_date = source_dates[source_pos]
                xs.append(source_returns[source_date])
                ys.append(target_ret)
            corr = _pearson(xs, ys)
            if corr is None:
                continue
            item = {"lag_days": lag, "correlation": round(corr, 4), "sample_size": len(xs)}
            if best is None or abs(item["correlation"]) > abs(best["correlation"]):
                best = item
        if best:
            rows.append({
                "source_code": code,
                "source_name": meta_map.get(code, {}).get("name", code),
                "target_code": target_code,
                "target_name": meta_map.get(target_code, {}).get("name", target_code),
                **best,
                "interpretation": "선행" if best["lag_days"] > 0 else "후행" if best["lag_days"] < 0 else "동행",
            })
    rows.sort(key=lambda r: abs(r["correlation"]), reverse=True)
    return rows[:limit]


def _infer_orphan_meta(code: str) -> dict:
    """Return display metadata for collected series not yet registered in categories."""
    c = (code or "").upper()
    if c.startswith("KR_"):
        category = "KOREA"
    elif c.startswith("US_"):
        category = "US"
    elif c.startswith("EU_"):
        category = "EU"
    elif c.startswith("CN_"):
        category = "CN"
    elif c.startswith("JP_"):
        category = "JP"
    elif c.startswith("COMM_"):
        category = "COMMODITY"
    else:
        category = "GLOBAL"

    labels = {
        "EU_EUR_USD": ("유로/달러 환율", "EUR/USD Exchange Rate", "FX", "달러"),
        "EU_DAX": ("독일 DAX 지수", "DAX Index", "MARKET", "포인트"),
        "EU_FTSE": ("영국 FTSE 100 지수", "FTSE 100 Index", "MARKET", "포인트"),
        "JP_NIKKEI": ("일본 닛케이 225 지수", "Nikkei 225 Index", "MARKET", "포인트"),
        "US_10Y_YIELD_YH": ("미국 10년 국채수익률(Yahoo)", "10Y Treasury Yield (Yahoo)", "BOND", "%"),
        "US_CPI_WB": ("미국 CPI(전년비, World Bank)", "US CPI YoY (World Bank)", "INFLATION", "%"),
        "US_UNEMPLOYMENT_WB": ("미국 실업률(World Bank)", "US Unemployment (World Bank)", "EMPLOYMENT", "%"),
    }
    name, name_en, subcategory, unit = labels.get(c, (c.replace("_", " "), c, "UNMAPPED", ""))
    return {
        "code": c,
        "name": name,
        "name_en": name_en,
        "category": category,
        "subcategory": subcategory,
        "unit": unit,
        "source": "AUTO",
        "frequency": "DAILY",
        "importance": 1,
        "description": "global_macro_data에 수집됐지만 카테고리 메타데이터가 아직 없는 지표입니다.",
        "is_active": 1,
    }


def _latest_orphans(conn, category: Optional[str] = None, limit: int = 100) -> list[dict]:
    rows = conn.execute("""
        SELECT d.indicator_code AS code, d.date, d.value, d.prev_value, d.change_pct
        FROM global_macro_data d
        LEFT JOIN global_macro_categories c ON c.code = d.indicator_code
        WHERE c.code IS NULL
          AND d.value IS NOT NULL
          AND d.date = (
              SELECT MAX(date) FROM global_macro_data
              WHERE indicator_code = d.indicator_code AND value IS NOT NULL
          )
        ORDER BY d.indicator_code
        LIMIT ?
    """, (limit,)).fetchall()
    result = []
    wanted = category.upper() if category else None
    for r in rows:
        meta = _infer_orphan_meta(r["code"])
        if wanted and meta["category"] != wanted:
            continue
        item = {**meta, "date": r["date"], "value": r["value"],
                "prev_value": r["prev_value"], "change_pct": r["change_pct"]}
        result.append(item)
    return result


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@router.get("/categories")
def get_categories(category: Optional[str] = None):
    """지표 카테고리 목록 (category: KOREA/US/EU/CN/JP/COMMODITY/GLOBAL)"""
    def _q():
        conn = _conn()
        q = "SELECT * FROM global_macro_categories WHERE is_active=1"
        params = []
        if category:
            q += " AND category=?"
            params.append(category.upper())
        q += " ORDER BY category, importance DESC, name"
        rows = conn.execute(q, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    return _cached(f"cats_{category}", _q)


@router.get("/latest")
def get_latest(
    category: Optional[str] = None,
    importance: int = Query(1, ge=1, le=3),
    limit: int = Query(100)
):
    """각 지표별 최신값 + 전월대비 변화"""
    def _q():
        conn = _conn()
        cat_filter = "AND c.category=?" if category else ""
        params = [importance]
        if category:
            params.append(category.upper())
        rows = conn.execute(f"""
            SELECT c.code, c.name, c.name_en, c.category, c.subcategory,
                   c.unit, c.source, c.frequency, c.importance,
                   d.date, d.value, d.prev_value, d.change_pct
            FROM global_macro_categories c
            LEFT JOIN global_macro_data d ON d.indicator_code = c.code
                AND d.date = (
                    SELECT MAX(date) FROM global_macro_data
                    WHERE indicator_code = c.code AND value IS NOT NULL
                )
            WHERE c.is_active=1 AND c.importance >= ?
            {cat_filter}
            ORDER BY c.category, c.importance DESC, c.name
            LIMIT ?
        """, params + [limit]).fetchall()
        data = _enrich_latest_rows(conn, [dict(r) for r in rows])
        if len(data) < limit:
            data.extend(_latest_orphans(conn, category=category, limit=limit - len(data)))
        conn.close()
        return data
    return _cached(f"latest_{category}_{importance}", _q)


@router.get("/timeseries/{code}")
def get_timeseries(code: str, days: int = Query(365)):
    """특정 지표 시계열 데이터"""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = _conn()
    meta = conn.execute(
        "SELECT * FROM global_macro_categories WHERE code=?", (code.upper(),)
    ).fetchone()
    rows = conn.execute("""
        SELECT date, value
        FROM global_macro_data
        WHERE indicator_code=? AND value IS NOT NULL
        ORDER BY date
    """, (code.upper(),)).fetchall()
    conn.close()
    meta_dict = dict(meta) if meta else _infer_orphan_meta(code)
    all_rows = _build_series_metrics([dict(r) for r in rows], meta_dict.get("frequency"))
    return {
        "meta": meta_dict,
        "data": [r for r in all_rows if r["date"] >= since]
    }


@router.get("/dashboard")
def get_dashboard():
    """대시보드용 핵심 지표 묶음"""
    def _q():
        conn = _conn()
        # 중요도 2 이상만
        rows = conn.execute("""
            SELECT c.code, c.name, c.category, c.subcategory,
                   c.name_en, c.unit, c.source, c.frequency, c.importance,
                   d.date, d.value, d.prev_value, d.change_pct
            FROM global_macro_categories c
            LEFT JOIN global_macro_data d ON d.indicator_code = c.code
                AND d.date = (
                    SELECT MAX(date) FROM global_macro_data
                    WHERE indicator_code = c.code AND value IS NOT NULL
                )
            WHERE c.is_active=1 AND c.importance >= 2
            ORDER BY c.importance DESC, c.category, c.name
        """).fetchall()
        orphans = _latest_orphans(conn, category=None, limit=100)
        latest_rows = _enrich_latest_rows(conn, [dict(x) for x in rows])
        result = {}
        for r in latest_rows + orphans:
            cat = r["category"]
            if cat not in result:
                result[cat] = []
            result[cat].append(r)
        korea_idx = {item["code"]: item for item in result.get("KOREA", [])}
        us_idx = {item["code"]: item for item in result.get("US", [])}
        eu_idx = {item["code"]: item for item in result.get("EU", [])}
        cn_idx = {item["code"]: item for item in result.get("CN", [])}
        jp_idx = {item["code"]: item for item in result.get("JP", [])}
        result["__focus"] = {
            "korea": [korea_idx[code] for code in _KOREA_FOCUS_CODES if code in korea_idx],
            "us": [us_idx[code] for code in _US_FOCUS_CODES if code in us_idx],
            "eu": [eu_idx[code] for code in _EU_FOCUS_CODES if code in eu_idx],
            "cn": [cn_idx[code] for code in _CN_FOCUS_CODES if code in cn_idx],
            "jp": [jp_idx[code] for code in _JP_FOCUS_CODES if code in jp_idx],
        }
        result["__signals"] = {
            "us": _build_us_signal(conn),
            "week4_regions": _build_week4_regions(conn),
            "week6_commodities": {
                "commodities": list(_latest_code_map(conn, _WEEK6_COMMODITY_CODES).values()),
                "fx": list(_latest_code_map(conn, _WEEK6_FX_CODES).values()),
                "oil_supply": list(_latest_code_map(conn, _WEEK6_OIL_SUPPLY_CODES).values()),
                "food": list(_latest_code_map(conn, _WEEK6_FOOD_CODES).values()),
                "correlations": _commodity_sector_correlations(conn, days=365, limit=8),
            },
            "insights": _build_macro_insights(conn, limit=12),
            "regime": _build_market_regime(conn),
            "lead_lag": _lead_lag_correlations(conn, limit=8),
        }
        conn.close()
        return result
    return _cached("dashboard", _q)


@router.get("/events")
def get_events(days_ahead: int = Query(30), days_behind: int = Query(7)):
    """경제 이벤트 캘린더"""
    since = (datetime.now() - timedelta(days=days_behind)).strftime("%Y-%m-%d")
    until = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    conn = _conn()
    rows = conn.execute("""
        SELECT * FROM global_macro_events
        WHERE event_date BETWEEN ? AND ?
        ORDER BY event_date, importance DESC
    """, (since, until)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.post("/events")
def upsert_event(body: dict):
    """이벤트 수동 등록/수정"""
    for key in [
        "event_time", "country", "indicator_code", "forecast", "previous",
        "actual", "unit", "surprise_value", "surprise_pct", "surprise_basis",
        "status", "source",
    ]:
        body.setdefault(key, None)
    body.setdefault("importance", 1)
    conn = _conn()
    conn.execute("""
        INSERT INTO global_macro_events
        (event_date,event_time,country,indicator_code,event_name,importance,forecast,previous,actual,unit,
         surprise_value,surprise_pct,surprise_basis,status,source,updated_at)
        VALUES (:event_date,:event_time,:country,:indicator_code,:event_name,
                :importance,:forecast,:previous,:actual,:unit,
                :surprise_value,:surprise_pct,:surprise_basis,
                COALESCE(:status,'manual'),COALESCE(:source,'manual'),datetime('now'))
        ON CONFLICT(event_date, COALESCE(country,''), COALESCE(indicator_code,''), event_name)
        DO UPDATE SET
            event_time=excluded.event_time,
            importance=excluded.importance,
            forecast=excluded.forecast,
            previous=excluded.previous,
            actual=excluded.actual,
            unit=excluded.unit,
            surprise_value=excluded.surprise_value,
            surprise_pct=excluded.surprise_pct,
            surprise_basis=excluded.surprise_basis,
            status=excluded.status,
            source=excluded.source,
            updated_at=excluded.updated_at
    """, body)
    conn.commit()
    conn.close()
    _cache.clear()
    return {"ok": True}


@router.get("/events/surprises")
def get_event_surprises(days: int = Query(90)):
    """최근 이벤트 서프라이즈 요약"""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = _conn()
    rows = conn.execute("""
        SELECT event_date, country, indicator_code, event_name, importance,
               forecast, previous, actual, unit, surprise_value, surprise_pct,
               surprise_basis, status, source
        FROM global_macro_events
        WHERE event_date >= ?
          AND actual IS NOT NULL
          AND (surprise_value IS NOT NULL OR surprise_pct IS NOT NULL)
        ORDER BY ABS(COALESCE(surprise_pct, surprise_value, 0)) DESC, event_date DESC
        LIMIT 100
    """, (since,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/events/reactions")
def get_event_reactions(days: int = Query(365), limit: int = Query(200)):
    """경제 이벤트 이후 주요 시장/팩터 반응"""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = _conn()
    rows = conn.execute("""
        SELECT event_id, event_date, country, indicator_code, event_name,
               surprise_value, surprise_pct, asset_code, asset_name, asset_group,
               "window", base_date, end_date, base_close, end_close, return_pct,
               direction, impact_score, created_at
        FROM global_macro_event_reactions
        WHERE event_date >= ?
        ORDER BY event_date DESC, ABS(COALESCE(impact_score, return_pct, 0)) DESC
        LIMIT ?
    """, (since, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/commodities")
def get_commodities():
    """Week6 원자재·환율 핵심 지표 최신값"""
    conn = _conn()
    result = {
        "commodities": list(_latest_code_map(conn, _WEEK6_COMMODITY_CODES).values()),
        "fx": list(_latest_code_map(conn, _WEEK6_FX_CODES).values()),
        "oil_supply": list(_latest_code_map(conn, _WEEK6_OIL_SUPPLY_CODES).values()),
        "food": list(_latest_code_map(conn, _WEEK6_FOOD_CODES).values()),
    }
    conn.close()
    return result


@router.get("/commodities/correlations")
def get_commodity_correlations(days: int = Query(365), limit: int = Query(30)):
    """원자재 일간 수익률과 국내 섹터지수 일간 수익률 상관관계"""
    conn = _conn()
    rows = _commodity_sector_correlations(conn, days=days, limit=limit)
    conn.close()
    return rows


@router.get("/insights")
def get_macro_insights(limit: int = Query(30)):
    """매크로 데이터에서 자동 추출한 파생 인사이트"""
    conn = _conn()
    result = _build_macro_insights(conn, limit=limit)
    result["regime"] = _build_market_regime(conn)
    result["lead_lag"] = _lead_lag_correlations(conn, limit=8)
    conn.close()
    return result


@router.get("/insights/regime")
def get_market_regime():
    """매크로 기반 현재 시장 국면 점수"""
    conn = _conn()
    result = _build_market_regime(conn)
    conn.close()
    return result


@router.get("/insights/lead-lag")
def get_lead_lag(target_code: str = Query("KR_KOSPI"), days: int = Query(730), limit: int = Query(12)):
    """주요 매크로 지표와 목표 지수의 리드-래그 상관관계"""
    conn = _conn()
    rows = _lead_lag_correlations(conn, target_code=target_code.upper(), days=days, limit=limit)
    conn.close()
    return rows


@router.post("/collect")
def trigger_collect(background_tasks: BackgroundTasks, source: str = Query("all")):
    """데이터 수집 즉시 실행"""
    background_tasks.add_task(_collect_task, source)
    return {"ok": True, "message": f"{source} 수집 시작"}


def _collect_task(source: str):
    sources = ["world_bank", "fred", "ecos", "yahoo", "kosis", "reb_housing", "global_financial", "dram_spot", "market_quant", "oecd_cli", "imf_weo", "events", "event_reactions", "fao_food", "eia_oil"] if source == "all" else [source]
    for s in sources:
        try:
            if s == "world_bank":
                from collectors.world_bank_collector import collect_world_bank
                collect_world_bank()
            elif s == "fred":
                from collectors.fred_collector import collect_fred
                collect_fred()
            elif s == "ecos":
                from collectors.ecos_collector import collect_ecos
                collect_ecos()
            elif s == "yahoo":
                from collectors.yahoo_macro_collector import collect_yahoo_macro
                collect_yahoo_macro()
            elif s == "kosis":
                from collectors.kosis_collector import collect_kosis
                collect_kosis()
            elif s == "reb_housing":
                from collectors.reb_housing_collector import collect_reb_housing
                collect_reb_housing()
            elif s == "global_financial":
                from collectors.global_financial_conditions_collector import collect_global_financial_conditions
                collect_global_financial_conditions()
            elif s == "dram_spot":
                from collectors.dram_spot_collector import collect_dram_spot
                collect_dram_spot()
            elif s == "market_quant":
                from collectors.market_quant_bridge_collector import collect_market_quant_bridge
                collect_market_quant_bridge()
            elif s == "oecd_cli":
                from collectors.oecd_cli_collector import collect_oecd_cli
                collect_oecd_cli()
            elif s == "imf_weo":
                from collectors.imf_weo_collector import collect_imf_weo
                collect_imf_weo()
            elif s == "events":
                from collectors.global_macro_event_collector import collect_global_macro_events
                collect_global_macro_events()
            elif s == "event_reactions":
                from collectors.global_macro_event_reaction_collector import collect_global_macro_event_reactions
                collect_global_macro_event_reactions()
            elif s == "fao_food":
                from collectors.fao_food_price_collector import collect_fao_food_prices
                collect_fao_food_prices()
            elif s == "eia_oil":
                from collectors.eia_oil_supply_collector import collect_eia_oil_supply
                collect_eia_oil_supply()
        except Exception as e:
            logger.error(f"collect_task [{s}] error: {e}")
            _log_collection(s, "error", 0, str(e))
    _cache.clear()


def _log_collection(source: str, status: str, records: int, message: str = ""):
    try:
        conn = _sl.connect(DB_PATH, timeout=30)
        conn.execute("""
            INSERT INTO global_macro_collection_log (source,status,records,message)
            VALUES (?,?,?,?)
        """, (source, status, records, message))
        conn.commit()
        conn.close()
    except Exception:
        pass


@router.get("/collection-log")
def get_collection_log(limit: int = Query(50)):
    """수집 로그 조회"""
    conn = _conn()
    rows = conn.execute("""
        SELECT * FROM global_macro_collection_log
        ORDER BY run_at DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/roadmap")
def get_roadmap():
    """12주 구축 로드맵"""
    conn = _conn()
    week2 = _week2_progress(conn)
    week3 = _week3_progress(conn)
    week4 = _week4_progress(conn)
    week5 = _week5_progress(conn)
    week6 = _week6_progress(conn)
    conn.close()
    roadmap = [
        {
            "week": 1,
            "title": "기반 구축 & 무료 데이터 연동",
            "description": "DB 스키마 설계, World Bank·IMF·Yahoo Finance 자동수집, 로드맵 대시보드",
            "status": "done",
            "tasks": [
                {"done": True, "text": "DB 테이블 설계 (global_macro_categories/data/events)"},
                {"done": True, "text": "지표 메타데이터 75종 등록"},
                {"done": True, "text": "World Bank API 수집기 구현 (무료, 키 불필요) — 120건"},
                {"done": True, "text": "Yahoo Finance 원자재·환율·지수 수집기 — 3,529건"},
                {"done": True, "text": "통계청 KOSIS 수집기 구현 (경기종합지수 22종) — 1,298건"},
                {"done": True, "text": "FRED 수집기 구현 (FRED_API_KEY 등록 후 활성화)"},
                {"done": True, "text": "ECOS 수집기 구현 (ECOS_API_KEY 등록 후 활성화)"},
                {"done": True, "text": "글로벌 경제 인텔리전스 프론트엔드 대시보드"},
            ],
            "apis": [
                {"name": "World Bank Open Data", "url": "https://api.worldbank.org/v2/", "free": True, "key": False},
                {"name": "Yahoo Finance", "url": "yfinance 라이브러리", "free": True, "key": False},
                {"name": "통계청 KOSIS", "url": "https://kosis.kr/openapi/", "free": True, "key": True, "key_name": "KOSIS_API_KEY", "registered": True},
                {"name": "FRED (Federal Reserve)", "url": "https://fred.stlouisfed.org/", "free": True, "key": True, "key_name": "FRED_API_KEY"},
                {"name": "한국은행 ECOS", "url": "https://ecos.bok.or.kr/", "free": True, "key": True, "key_name": "ECOS_API_KEY"},
            ]
        },
        {
            "week": 2,
            "title": "한국 핵심 경제지표 완성",
            "description": "한국은행 ECOS API 전 지표 수집, KOSIS 통계청 연동, 한국 경제 대시보드",
            "status": week2["status"],
            "tasks": [
                {"done": week2["ecos_ready"] == week2["ecos_total"], "text": f"ECOS API 전 지표 수집 (기준금리/CPI/GDP/M2/무역수지/경상수지) — {week2['ecos_ready']}/{week2['ecos_total']}"},
                {"done": week2["kosis_ready"] == week2["kosis_total"], "text": f"KOSIS 통계청 API 연동 (산업생산/소매판매/고용) — {week2['kosis_ready']}/{week2['kosis_total']}"},
                {"done": week2["housing_ready"], "text": "한국 주택가격지수 수집"},
                {"done": True, "text": "한국 경제 전용 대시보드 UI"},
                {"done": True, "text": "전월대비/전년대비 자동 계산"},
            ],
            "apis": [
                {"name": "한국은행 ECOS", "url": "https://ecos.bok.or.kr/", "free": True, "key": True, "key_name": "ECOS_API_KEY"},
                {"name": "통계청 KOSIS", "url": "https://kosis.kr/openapi/", "free": True, "key": True, "key_name": "KOSIS_API_KEY"},
            ]
        },
        {
            "week": 3,
            "title": "미국 경제지표 완성 (FRED)",
            "description": "FRED API 전 지표 수집, 연준 정책 추적, 수익률 곡선 분석",
            "status": week3["status"],
            "tasks": [
                {"done": week3["fred_ready"] == week3["fred_total"], "text": f"FRED 전 지표 수집 (금리/CPI/GDP/고용/소비/주택) — {week3['fred_ready']}/{week3['fred_total']}"},
                {"done": week3["yield_curve_ready"], "text": "미국 수익률 곡선 (Yield Curve) 시각화"},
                {"done": False, "text": "FOMC 회의 일정 및 점도표 추적"},
                {"done": True, "text": "미국 경제 전용 대시보드"},
                {"done": week3["spread_ready"], "text": "장단기 금리차(2Y-10Y) 경기침체 신호"},
            ],
            "apis": [
                {"name": "FRED", "url": "https://fred.stlouisfed.org/", "free": True, "key": True, "key_name": "FRED_API_KEY"},
            ]
        },
        {
            "week": 4,
            "title": "글로벌 지표 & OECD/IMF",
            "description": "유럽·중국·일본 지표, OECD 경기선행지수, IMF WEO 전망치",
            "status": week4["status"],
            "tasks": [
                {"done": week4["oecd_ready"] == week4["oecd_total"], "text": f"OECD CLI 경기선행지수 수집 — {week4['oecd_ready']}/{week4['oecd_total']}"},
                {"done": week4["imf_ready"] == week4["imf_total"], "text": f"IMF World Economic Outlook 전망치 — {week4['imf_ready']}/{week4['imf_total']}"},
                {"done": week4["eu_ready"] >= 3, "text": f"유럽(EU/독일/프랑스) 핵심 지표 — {week4['eu_ready']}/{week4['eu_total']}"},
                {"done": week4["cn_ready"] >= 3, "text": f"중국(PMI/무역수지/외환보유고) 지표 — {week4['cn_ready']}/{week4['cn_total']}"},
                {"done": week4["jp_ready"] >= 3, "text": f"일본(BOJ/CPI/수출) 지표 — {week4['jp_ready']}/{week4['jp_total']}"},
            ],
            "apis": [
                {"name": "OECD API", "url": "https://stats.oecd.org/", "free": True, "key": False},
                {"name": "IMF Data API", "url": "https://www.imf.org/en/Data", "free": True, "key": False},
            ]
        },
        {
            "week": 5,
            "title": "경제 이벤트 캘린더",
            "description": "주요 경제지표 발표 일정, 예상치 vs 실제치 추적, 서프라이즈 분석",
            "status": week5["status"],
            "tasks": [
                {"done": week5["events_window_count"] > 0, "text": f"경제 이벤트 캘린더 UI/API (최근 2주~45일) — {week5['events_window_count']}건"},
                {"done": True, "text": "예상치(Forecast) vs 실제치(Actual) 비교 필드/수동 upsert"},
                {"done": week5["surprise_events"] > 0, "text": f"경제 서프라이즈 계산(actual-forecast/actual-previous) — {week5['surprise_events']}건"},
                {"done": week5["upcoming_events"] > 0, "text": f"발표 전 일정 추적 — 향후 {week5['upcoming_events']}건"},
                {"done": week5["reaction_links"] > 0, "text": f"투자에스팩터 링크 (이벤트→주요 지수/팩터 반응) — {week5['reaction_links']}건"},
            ]
        },
        {
            "week": 6,
            "title": "원자재 & 환율 심층 분석",
            "description": "원자재 가격 추세, 환율 변동 분석, 원자재-주가 상관관계",
            "status": week6["status"],
            "tasks": [
                {"done": week6["commodity_ready"] >= 5, "text": f"원자재 가격 시계열 대시보드 (유가/금/구리/천연가스/소맥 등) — {week6['commodity_ready']}/{week6['commodity_total']}"},
                {"done": week6["fx_ready"] >= 5, "text": f"주요 환율 실시간 추적 (USD/KRW/JPY/CNY/EUR) — {week6['fx_ready']}/{week6['fx_total']}"},
                {"done": week6["correlation_ready"], "text": "원자재-섹터 상관관계 분석"},
                {"done": week6["oil_supply_ready"] >= 1, "text": f"원유 수급(재고/생산) 지표 — {week6['oil_supply_ready']}/{week6['oil_supply_total']}"},
                {"done": week6["food_ready"], "text": "FAO 식품가격지수 수집"},
            ]
        },
        {
            "week": 7,
            "title": "AI 인사이트 엔진 v1",
            "description": "GPT 기반 경제지표 해석, 주간 리포트 자동 생성",
            "status": "planned",
            "tasks": [
                {"done": False, "text": "주요 지표 변화 자동 감지 (임계값 기반)"},
                {"done": False, "text": "GPT-4o 경제지표 해석 엔진"},
                {"done": False, "text": "주간 글로벌 경제 리포트 자동 생성"},
                {"done": False, "text": "텔레그램 경제 브리핑 발송"},
                {"done": False, "text": "주요 지표 발표 시 즉시 AI 분석"},
            ]
        },
        {
            "week": 8,
            "title": "알림 & 모니터링 시스템",
            "description": "지표 임계값 알림, 이상 감지, 대시보드 실시간 갱신",
            "status": "planned",
            "tasks": [
                {"done": False, "text": "지표 임계값 알림 설정 UI"},
                {"done": False, "text": "텔레그램 경제지표 알림 봇"},
                {"done": False, "text": "이상치 자동 감지 (Z-score 기반)"},
                {"done": False, "text": "대시보드 자동 갱신 스케줄러"},
            ]
        },
        {
            "week": 9,
            "title": "경기 사이클 분석",
            "description": "경기 확장/수축 국면 판단, 섹터 로테이션 신호",
            "status": "planned",
            "tasks": [
                {"done": False, "text": "경기선행지수(CLI) 기반 사이클 판단"},
                {"done": False, "text": "수익률 곡선 역전 경기침체 신호"},
                {"done": False, "text": "경기 국면별 섹터 성과 분석"},
                {"done": False, "text": "글로벌 경기 동조화 분석"},
            ]
        },
        {
            "week": 10,
            "title": "거시경제-주가 상관관계",
            "description": "경제지표와 KOSPI/KOSDAQ/섹터 간 상관관계 분석",
            "status": "planned",
            "tasks": [
                {"done": False, "text": "주요 지표-주가지수 상관관계 매트릭스"},
                {"done": False, "text": "지표 선행성 분석 (Lead/Lag 분석)"},
                {"done": False, "text": "경제지표 발표 전후 시장 반응 분석"},
                {"done": False, "text": "인플레이션-금리-주가 삼각관계 분석"},
            ]
        },
        {
            "week": 11,
            "title": "국가별 비교 대시보드",
            "description": "G20 국가 종합 비교, 한국 경쟁력 분석",
            "status": "planned",
            "tasks": [
                {"done": False, "text": "G20 국가별 GDP·성장률·물가 비교표"},
                {"done": False, "text": "한국 경제 글로벌 순위 추적"},
                {"done": False, "text": "신흥국 vs 선진국 지표 비교"},
                {"done": False, "text": "국가 리스크 지표 (CDS, 외환보유고)"},
            ]
        },
        {
            "week": 12,
            "title": "시나리오 분석 & 투자 시그널",
            "description": "경제 시나리오별 투자 전략, 전체 시스템 완성",
            "status": "planned",
            "tasks": [
                {"done": False, "text": "거시경제 시나리오 시뮬레이터"},
                {"done": False, "text": "경제지표 기반 투자 시그널 생성"},
                {"done": False, "text": "자산 배분 권고 시스템"},
                {"done": False, "text": "전체 대시보드 통합 및 최적화"},
            ]
        },
    ]
    return {"roadmap": roadmap, "total_weeks": 12, "current_week": 6}


@router.get("/stats")
def get_stats():
    """수집 현황 통계"""
    conn = _conn()
    total_cats = conn.execute("SELECT COUNT(*) FROM global_macro_categories WHERE is_active=1").fetchone()[0]
    orphan_cats = conn.execute("""
        SELECT COUNT(DISTINCT d.indicator_code)
        FROM global_macro_data d
        LEFT JOIN global_macro_categories c ON c.code = d.indicator_code
        WHERE c.code IS NULL AND d.value IS NOT NULL
    """).fetchone()[0]
    total_data = conn.execute("SELECT COUNT(*) FROM global_macro_data WHERE value IS NOT NULL").fetchone()[0]
    by_cat = conn.execute("""
        SELECT c.category, COUNT(DISTINCT c.code) cats, COUNT(d.id) records
        FROM global_macro_categories c
        LEFT JOIN global_macro_data d ON d.indicator_code=c.code AND d.value IS NOT NULL
        WHERE c.is_active=1
        GROUP BY c.category ORDER BY c.category
    """).fetchall()
    orphan_by_cat = {}
    for r in conn.execute("""
        SELECT d.indicator_code, COUNT(*) records
        FROM global_macro_data d
        LEFT JOIN global_macro_categories c ON c.code = d.indicator_code
        WHERE c.code IS NULL AND d.value IS NOT NULL
        GROUP BY d.indicator_code
    """).fetchall():
        cat = _infer_orphan_meta(r["indicator_code"])["category"]
        if cat not in orphan_by_cat:
            orphan_by_cat[cat] = {"category": cat, "cats": 0, "records": 0}
        orphan_by_cat[cat]["cats"] += 1
        orphan_by_cat[cat]["records"] += r["records"]
    last_log = conn.execute("""
        SELECT source, status, records, run_at
        FROM global_macro_collection_log
        ORDER BY run_at DESC LIMIT 5
    """).fetchall()
    week2 = _week2_progress(conn)
    week3 = _week3_progress(conn)
    week4 = _week4_progress(conn)
    week5 = _week5_progress(conn)
    week6 = _week6_progress(conn)
    conn.close()
    by_category = [dict(r) for r in by_cat]
    by_cat_idx = {r["category"]: r for r in by_category}
    for cat, item in orphan_by_cat.items():
        if cat in by_cat_idx:
            by_cat_idx[cat]["cats"] += item["cats"]
            by_cat_idx[cat]["records"] += item["records"]
        else:
            by_category.append(item)
    by_category.sort(key=lambda x: x["category"])
    return {
        "total_indicators": total_cats + orphan_cats,
        "total_data_points": total_data,
        "by_category": by_category,
        "recent_collections": [dict(r) for r in last_log],
        "week2_progress": week2,
        "week3_progress": week3,
        "week4_progress": week4,
        "week5_progress": week5,
        "week6_progress": week6,
    }
