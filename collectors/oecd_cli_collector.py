"""
OECD Composite Leading Indicators 수집기
공식 API 예시:
https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_STES@DF_CLI/.M.LI...AA...H?startPeriod=2023-02&dimensionAtObservation=AllDimensions&format=csvfilewithlabels
"""
import csv
import io
import logging
import sqlite3
import time
from datetime import datetime, timedelta

import requests

logger = logging.getLogger(__name__)
DB_PATH = "stock.db"
URL = "https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_STES@DF_CLI/.M.LI...AA...H"

AREA_TO_CODE = {
    "USA": "US_CLI_OECD",
    "CHN": "CN_CLI_OECD",
    "JPN": "JP_CLI_OECD",
}


def _fetch(start_period: str) -> list[dict]:
    params = {
        "startPeriod": start_period,
        "dimensionAtObservation": "AllDimensions",
        "format": "csvfilewithlabels",
    }
    resp = requests.get(URL, params=params, timeout=30)
    resp.raise_for_status()
    return list(csv.DictReader(io.StringIO(resp.text)))


def collect_oecd_cli(lookback_years: int = 5) -> int:
    start_period = (datetime.now() - timedelta(days=lookback_years * 365)).strftime("%Y-%m")
    try:
        rows = _fetch(start_period)
    except Exception as e:
        logger.warning(f"OECD CLI fetch failed: {e}")
        _log(0, "error", str(e))
        return 0

    by_code: dict[str, list[tuple[str, float]]] = {}
    for row in rows:
        area = row.get("REF_AREA")
        our_code = AREA_TO_CODE.get(area or "")
        if not our_code:
            continue
        period = row.get("TIME_PERIOD", "")
        value = row.get("OBS_VALUE", "")
        if not period or not value:
            continue
        try:
            val = float(value)
            date = f"{period}-01"
            by_code.setdefault(our_code, []).append((date, val))
        except ValueError:
            continue

    conn = sqlite3.connect(DB_PATH, timeout=30)
    total = 0
    for our_code, values in by_code.items():
        values.sort(key=lambda x: x[0])
        for i, (date, val) in enumerate(values):
            prev = values[i - 1][1] if i > 0 else None
            chg = ((val - prev) / abs(prev) * 100) if prev else None
            conn.execute("""
                INSERT INTO global_macro_data (indicator_code, date, value, prev_value, change_pct)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(indicator_code, date) DO UPDATE SET
                    value=excluded.value,
                    prev_value=excluded.prev_value,
                    change_pct=excluded.change_pct
            """, (our_code, date, val, prev, chg))
            total += 1
        time.sleep(0.1)
    conn.commit()
    conn.close()
    _log(total)
    logger.info(f"OECD CLI collected {total} records")
    return total


def _log(records: int, status: str = "ok", msg: str = ""):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.execute("""
            INSERT INTO global_macro_collection_log (source, status, records, message)
            VALUES ('oecd_cli', ?, ?, ?)
        """, (status, records, msg))
        conn.commit()
        conn.close()
    except Exception:
        pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(collect_oecd_cli())
