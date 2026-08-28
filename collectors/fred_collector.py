"""
FRED (Federal Reserve Economic Data) 수집기
무료 API 키 필요: https://fred.stlouisfed.org/docs/api/api_key.html
환경변수: FRED_API_KEY
"""
import sqlite3, requests, logging, os, time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)
DB_PATH = "stock.db"

# (fred_series_id, our_code, transform)
# transform: None=원본, 'pct_change'=전월대비%, 'yoy'=전년대비%
FRED_SERIES = [
    ("FEDFUNDS",        "US_FED_RATE",       None),        # 기준금리
    ("CPIAUCSL",        "US_CPI",            "yoy"),        # CPI 전년비
    ("CPILFESL",        "US_CORE_CPI",       "yoy"),        # Core CPI 전년비
    ("PCEPI",           "US_PCE",            "yoy"),        # PCE 전년비
    ("A191RL1Q225SBEA", "US_GDP_GROWTH",     None),         # 실질GDP 성장률(연율)
    ("UNRATE",          "US_UNEMPLOYMENT",   None),         # 실업률
    ("PAYEMS",          "US_NONFARM",        "diff"),        # 비농업고용자수 전월차
    ("RSAFS",           "US_RETAIL_SALES",   "pct_change"), # 소매판매 전월비
    ("UMCSENT",         "US_CONSUMER_CONF",  None),         # 소비자신뢰
    ("GS10",            "US_10Y_YIELD",      None),         # 10년 국채
    ("GS2",             "US_2Y_YIELD",       None),         # 2년 국채
    ("M2SL",            "US_M2",             None),         # M2
    ("HOUST",           "US_HOUSING_START",  None),         # 주택착공
]

BASE_URL = "https://api.fredapi.com/fred/series/observations"  # 공식 URL


def _get_api_key() -> str | None:
    key = os.getenv("FRED_API_KEY", "")
    if not key:
        logger.warning("FRED_API_KEY not set in environment. Skipping FRED collection.")
    return key or None


def _fetch_series(api_key: str, series_id: str, start_date: str) -> list[dict]:
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": start_date,
        "sort_order": "asc",
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get("observations", [])
    except Exception as e:
        logger.warning(f"FRED fetch failed [{series_id}]: {e}")
        return []


def _apply_transform(values: list[tuple], transform: str | None) -> list[tuple]:
    """(date, value) 리스트에 변환 적용, 반환: (date, value, prev_value, change_pct)"""
    result = []
    for i, (date, val) in enumerate(values):
        if val is None:
            continue
        prev = values[i - 1][1] if i > 0 else None
        if transform == "yoy":
            # 전년 같은 달 값 찾기
            year_ago_date = date[:4] + str(int(date[:4]) - 1) + date[4:]
            # 단순화: prev_value를 12개 전 값으로
            prev_yr = values[i - 12][1] if i >= 12 else None
            chg = ((val - prev_yr) / abs(prev_yr) * 100) if prev_yr else None
            result.append((date, val, prev_yr, chg))
        elif transform == "pct_change":
            chg = ((val - prev) / abs(prev) * 100) if prev else None
            result.append((date, val, prev, chg))
        elif transform == "diff":
            chg = (val - prev) if prev is not None else None
            result.append((date, val, prev, chg))
        else:
            result.append((date, val, prev, None))
    return result


def collect_fred(lookback_years: int = 5) -> int:
    api_key = _get_api_key()
    if not api_key:
        return 0

    start_date = (datetime.now() - timedelta(days=lookback_years * 365)).strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)
    total = 0

    for series_id, our_code, transform in FRED_SERIES:
        obs = _fetch_series(api_key, series_id, start_date)
        values = []
        for o in obs:
            v = o.get("value", ".")
            if v == ".":
                continue
            values.append((o["date"], float(v)))

        transformed = _apply_transform(values, transform)
        for date, val, prev, chg in transformed:
            conn.execute("""
                INSERT INTO global_macro_data (indicator_code, date, value, prev_value, change_pct)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(indicator_code, date) DO UPDATE SET
                    value=excluded.value, prev_value=excluded.prev_value, change_pct=excluded.change_pct
            """, (our_code, date, val, prev, chg))
            total += 1
        time.sleep(0.5)  # FRED rate limit: 120 req/min

    conn.commit()
    conn.close()
    _log(total)
    logger.info(f"FRED collected {total} records")
    return total


def _log(records: int, status: str = "ok", msg: str = ""):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            INSERT INTO global_macro_collection_log (source, status, records, message)
            VALUES ('fred', ?, ?, ?)
        """, (status, records, msg))
        conn.commit()
        conn.close()
    except Exception:
        pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    n = collect_fred()
    print(f"FRED 수집 완료: {n}건")
