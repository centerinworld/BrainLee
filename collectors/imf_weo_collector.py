"""
IMF World Economic Outlook 수집기
공식 Datamapper API 사용:
https://www.imf.org/external/datamapper/api/v1/NGDP_RPCH/USA,EUQ,CHN,JPN
"""
import logging
import sqlite3
from datetime import datetime

import requests

logger = logging.getLogger(__name__)
DB_PATH = "stock.db"
URL = "https://www.imf.org/external/datamapper/api/v1/NGDP_RPCH/USA,EUQ,CHN,JPN"

COUNTRY_TO_CODE = {
    "USA": "US_GDP_GROWTH_WEO",
    "EUQ": "EU_GDP_GROWTH_WEO",
    "CHN": "CN_GDP_GROWTH_WEO",
    "JPN": "JP_GDP_GROWTH_WEO",
}


def _fetch() -> dict:
    resp = requests.get(URL, timeout=30)
    resp.raise_for_status()
    return resp.json()


def collect_imf_weo(start_year: int = 2015) -> int:
    try:
        payload = _fetch()
    except Exception as e:
        logger.warning(f"IMF WEO fetch failed: {e}")
        _log(0, "error", str(e))
        return 0

    values = payload.get("values", {}).get("NGDP_RPCH", {})
    conn = sqlite3.connect(DB_PATH, timeout=30)
    total = 0

    for country_code, our_code in COUNTRY_TO_CODE.items():
        yearly = values.get(country_code, {})
        normalized = []
        for year_str, raw_value in yearly.items():
            try:
                year = int(year_str)
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
            if year < start_year:
                continue
            normalized.append((f"{year}-12-31", value))

        normalized.sort(key=lambda x: x[0])
        for idx, (date, value) in enumerate(normalized):
            prev = normalized[idx - 1][1] if idx > 0 else None
            chg = ((value - prev) / abs(prev) * 100.0) if prev not in (None, 0) else None
            conn.execute("""
                INSERT INTO global_macro_data (indicator_code, date, value, prev_value, change_pct)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(indicator_code, date) DO UPDATE SET
                    value=excluded.value,
                    prev_value=excluded.prev_value,
                    change_pct=excluded.change_pct
            """, (our_code, date, value, prev, chg))
            total += 1

    conn.commit()
    conn.close()
    _log(total)
    logger.info(f"IMF WEO collected {total} records at {datetime.now().isoformat(timespec='seconds')}")
    return total


def _log(records: int, status: str = "ok", msg: str = ""):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.execute("""
            INSERT INTO global_macro_collection_log (source, status, records, message)
            VALUES ('imf_weo', ?, ?, ?)
        """, (status, records, msg))
        conn.commit()
        conn.close()
    except Exception:
        pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(collect_imf_weo())
