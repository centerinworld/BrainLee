"""
Korea Real Estate Board housing price index collector.

Source: REB R-ONE public statistics page for monthly apartment sale price index.
This is used as a fallback/primary path when the KOSIS API key is unavailable.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime
from urllib.parse import parse_qsl

import requests

logger = logging.getLogger(__name__)
DB_PATH = "stock.db"

PAGE_URL = "https://www.reb.or.kr/r-one/portal/stat/easyStatPage/A_2024_00045.do"
DATA_URL = "https://www.reb.or.kr/r-one/portal/stat/sttsDataPreviewList.do"


def _params(start_year: int, end_year: int) -> list[tuple[str, str]]:
    query = (
        "statblId=A_2024_00045"
        "&viewLocOpt=B"
        "&wrttimeType=B"
        "&dtadvsVal=OD"
        "&wrttimeOrder=A"
        "&dtacycleCd=MM"
        f"&wrttimeStartYear={start_year}"
        f"&wrttimeEndYear={end_year}"
        "&wrttimeStartQt=01"
        "&wrttimeEndQt=12"
        "&wrttimeMinYear=2003"
        f"&wrttimeMaxYear={end_year}"
        "&wrttimeMinQt=01"
        "&wrttimeMaxQt=12"
        "&optDivVal=00"
        "&isRegionData=Y"
        "&statblNm=(월) 매매가격지수_아파트"
    )
    return parse_qsl(query, keep_blank_values=True)


def _fetch_reb_housing(start_year: int, end_year: int) -> list[tuple[str, float]]:
    session = requests.Session()
    session.get(PAGE_URL, timeout=20)
    response = session.post(
        DATA_URL,
        data=_params(start_year, end_year),
        timeout=40,
        headers={
            "Referer": PAGE_URL,
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("DATA") or []
    nationwide = next(
        (
            row
            for row in rows
            if row.get("CATE1") == "전국"
            and row.get("CATE2") == "전국"
            and row.get("CATE3") == "전국"
            and row.get("CATE4") == "전국"
        ),
        None,
    )
    if not nationwide:
        return []

    series: list[tuple[str, float]] = []
    for key, raw_value in nationwide.items():
        match = re.fullmatch(r"COL_(\d{6})\d+OD", key)
        if not match:
            continue
        ym = match.group(1)
        try:
            value = float(str(raw_value).replace(",", ""))
        except (TypeError, ValueError):
            continue
        series.append((f"{ym[:4]}-{ym[4:6]}-01", value))
    series.sort(key=lambda item: item[0])
    return series


def collect_reb_housing(start_year: int = 2021, end_year: int | None = None) -> int:
    end_year = end_year or datetime.now().year
    values = _fetch_reb_housing(start_year, end_year)
    if not values:
        _log("warning", 0, "REB housing endpoint returned no nationwide series")
        return 0

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        INSERT OR IGNORE INTO global_macro_categories
        (code,name,name_en,category,subcategory,unit,source,source_code,frequency,importance)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "KR_HOUSING_PRICE",
            "한국 주택매매가격지수",
            "Korea Housing Sale Price Index",
            "KOREA",
            "REALESTATE",
            "지수",
            "REB",
            "A_2024_00045",
            "MONTHLY",
            3,
        ),
    )

    total = 0
    for i, (date, value) in enumerate(values):
        prev = values[i - 1][1] if i > 0 else None
        change_pct = ((value - prev) / abs(prev) * 100.0) if prev else None
        conn.execute(
            """
            INSERT INTO global_macro_data (indicator_code, date, value, prev_value, change_pct)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(indicator_code, date) DO UPDATE SET
                value=excluded.value,
                prev_value=excluded.prev_value,
                change_pct=excluded.change_pct
            """,
            ("KR_HOUSING_PRICE", date, value, prev, change_pct),
        )
        total += 1
    conn.commit()
    conn.close()
    _log("ok", total, f"REB housing price index {values[0][0]}~{values[-1][0]}")
    logger.info("REB housing collected %s records", total)
    return total


def _log(status: str, records: int, message: str = "") -> None:
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.execute(
            """
            INSERT INTO global_macro_collection_log (source, status, records, message)
            VALUES ('reb_housing', ?, ?, ?)
            """,
            (status, records, message),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    count = collect_reb_housing()
    print(f"REB 주택가격지수 수집 완료: {count}건")
