"""
한국은행 ECOS API 수집기
무료 API 키 필요: https://ecos.bok.or.kr/api/#/DevGuide/TokenStat
환경변수: ECOS_API_KEY
"""
import sqlite3, requests, logging, os, time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)
DB_PATH = "stock.db"

BASE_URL = "https://ecos.bok.or.kr/api"

# (stat_code, cycle, item_code, our_code)
# ECOS cycle values are M/Q/A/D, not MM/QQ/YY.
ECOS_SERIES = [
    # 기준금리: 722Y001 / 0101000 (월)
    ("722Y001", "M", [("0101000", "KR_BASE_RATE")]),
    # CPI 총지수: 901Y009 / 0 (월, 2020=100)
    ("901Y009", "M", [("0", "KR_CPI")]),
    # 원/달러 환율 매매기준율: 731Y001 / 0000001 (일)
    ("731Y001", "D", [("0000001", "KR_USD_KRW")]),
    # 실업률: 901Y027 / I61BC (월)
    ("901Y027", "M", [("I61BC", "KR_UNEMPLOYMENT")]),
    # M2(광의통화) 말잔 원계열: 161Y008 / BBGA00 (월, 단위: 십억원)
    ("161Y008", "M", [("BBGA00", "KR_M2")]),
    # 국제수지(계절조정): 경상수지/상품수지/수출/수입 (월, 단위: 백만달러)
    ("301Y017", "M", [
        ("SA000", "KR_CURRENT_ACCOUNT"),
        ("SA100", "KR_TRADE_BALANCE"),
        ("SA110", "KR_EXPORT"),
        ("SA120", "KR_IMPORT"),
    ]),
]

VALUE_TRANSFORMS = {
    "KR_M2": lambda value: value / 100.0,  # 십억원 -> 조원
    "KR_CURRENT_ACCOUNT": lambda value: value / 100.0,  # 백만달러 -> 억달러
    "KR_TRADE_BALANCE": lambda value: value / 100.0,    # 백만달러 -> 억달러
    "KR_EXPORT": lambda value: value / 100.0,           # 백만달러 -> 억달러
    "KR_IMPORT": lambda value: value / 100.0,           # 백만달러 -> 억달러
}


def _get_api_key() -> str | None:
    key = os.getenv("ECOS_API_KEY", "")
    if not key:
        logger.warning("ECOS_API_KEY not set. Skipping ECOS collection.")
    return key or None


def _fetch(api_key: str, stat_code: str, cycle: str, item_code: str,
           start: str, end: str) -> list[dict]:
    url = (f"{BASE_URL}/StatisticSearch/{api_key}/json/kr/1/1000"
           f"/{stat_code}/{cycle}/{start}/{end}/{item_code}")
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("StatisticSearch", {}).get("row", [])
        return rows
    except Exception as e:
        logger.warning(f"ECOS fetch failed [{stat_code}/{item_code}]: {e}")
        return []


def _period_to_date(period: str, cycle: str) -> str:
    """ECOS 기간 표기 → YYYY-MM-DD"""
    p = period.strip()
    if cycle in ("M", "MM") and len(p) == 6:
        return f"{p[:4]}-{p[4:6]}-01"
    if cycle in ("Q", "QQ") and "Q" in p:
        y, q = p.split("Q")
        month = str(int(q) * 3).zfill(2)
        return f"{y}-{month}-01"
    if cycle in ("A", "Y", "YY") and len(p) == 4:
        return f"{p}-12-31"
    if cycle == "D" and len(p) == 8:
        return f"{p[:4]}-{p[4:6]}-{p[6:8]}"
    return p


def collect_ecos(lookback_years: int = 10) -> int:
    api_key = _get_api_key()
    if not api_key:
        return 0

    now = datetime.now()
    start_dt = now - timedelta(days=lookback_years * 365)
    conn = sqlite3.connect(DB_PATH)
    total = 0

    for stat_code, cycle, items in ECOS_SERIES:
        if cycle in ("M", "MM"):
            start = start_dt.strftime("%Y%m")
            end = now.strftime("%Y%m")
        elif cycle in ("Q", "QQ"):
            start = f"{start_dt.year}Q1"
            end = f"{now.year}Q{(now.month - 1) // 3 + 1}"
        elif cycle == "D":
            start = start_dt.strftime("%Y%m%d")
            end = now.strftime("%Y%m%d")
        else:
            start = str(start_dt.year)
            end = str(now.year)

        for item_code, our_code in items:
            rows = _fetch(api_key, stat_code, cycle, item_code, start, end)
            values = []
            for r in rows:
                period = r.get("TIME", "")
                val_str = r.get("DATA_VALUE", "")
                if not val_str or val_str == "-":
                    continue
                try:
                    val = float(val_str.replace(",", ""))
                    transform = VALUE_TRANSFORMS.get(our_code)
                    if transform:
                        val = transform(val)
                    date = _period_to_date(period, cycle)
                    values.append((date, val))
                except (ValueError, TypeError):
                    continue

            values.sort(key=lambda x: x[0])
            for i, (date, val) in enumerate(values):
                prev = values[i - 1][1] if i > 0 else None
                chg = ((val - prev) / abs(prev) * 100) if prev else None
                conn.execute("""
                    INSERT INTO global_macro_data (indicator_code, date, value, prev_value, change_pct)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(indicator_code, date) DO UPDATE SET
                        value=excluded.value, prev_value=excluded.prev_value, change_pct=excluded.change_pct
                """, (our_code, date, val, prev, chg))
                total += 1
            time.sleep(0.3)

    conn.commit()
    conn.close()
    _log(total)
    logger.info(f"ECOS collected {total} records")
    return total


def _log(records: int, status: str = "ok", msg: str = ""):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            INSERT INTO global_macro_collection_log (source, status, records, message)
            VALUES ('ecos', ?, ?, ?)
        """, (status, records, msg))
        conn.commit()
        conn.close()
    except Exception:
        pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    n = collect_ecos()
    print(f"ECOS 수집 완료: {n}건")
