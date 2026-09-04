"""
Global financial conditions and policy-rate collector.

Uses FRED official API series for policy rates, credit spreads, financial
conditions, breakeven inflation, and additional Treasury tenors.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

sys.path.append(str(Path(__file__).resolve().parents[1]))
import config  # noqa: F401  # load .env values when running collector directly

logger = logging.getLogger(__name__)
DB_PATH = "stock.db"

FRED_SERIES = [
    ("ECBMRRFR", "EU_ECB_RATE", None),
    ("IRSTCI01JPM156N", "JP_BOJ_RATE", None),
    ("BAMLH0A0HYM2", "US_HY_SPREAD", None),
    ("BAA10Y", "US_BAA_SPREAD", None),
    ("NFCI", "US_NFCI", None),
    ("T10YIE", "US_10Y_BREAKEVEN", None),
    ("DGS30", "US_30Y_YIELD", None),
    ("DGS3MO", "US_3M_YIELD", None),
]

CATEGORIES = [
    ("EU_ECB_RATE", "유럽 ECB 기준금리", "ECB Main Refinancing Rate", "EU", "MONETARY", "%", "FRED", "ECBMRRFR", "DAILY", 3),
    ("JP_BOJ_RATE", "일본 BOJ 단기금리", "Japan Call Money / Policy Rate Proxy", "JP", "MONETARY", "%", "FRED", "IRSTCI01JPM156N", "MONTHLY", 2),
    ("US_HY_SPREAD", "미국 하이일드 스프레드", "US High Yield OAS", "US", "CREDIT", "%p", "FRED", "BAMLH0A0HYM2", "DAILY", 3),
    ("US_BAA_SPREAD", "미국 Baa 회사채 스프레드", "US Baa Corporate Spread vs 10Y", "US", "CREDIT", "%p", "FRED", "BAA10Y", "DAILY", 2),
    ("US_NFCI", "미국 금융여건지수", "Chicago Fed National Financial Conditions Index", "US", "FINANCIAL_CONDITIONS", "지수", "FRED", "NFCI", "WEEKLY", 3),
    ("US_10Y_BREAKEVEN", "미국 10년 기대인플레이션", "10Y Breakeven Inflation Rate", "US", "INFLATION_EXPECTATION", "%", "FRED", "T10YIE", "DAILY", 3),
    ("US_30Y_YIELD", "미국 30년 국채수익률", "30Y Treasury Yield", "US", "BOND", "%", "FRED", "DGS30", "DAILY", 2),
    ("US_3M_YIELD", "미국 3개월 국채수익률", "3M Treasury Yield", "US", "BOND", "%", "FRED", "DGS3MO", "DAILY", 2),
]


def _get_api_key() -> str | None:
    key = os.getenv("FRED_API_KEY", "").strip()
    if not key:
        logger.warning("FRED_API_KEY not set. Skipping global financial conditions collection.")
        return None
    return key


def _fetch_series(api_key: str, series_id: str, start_date: str) -> list[tuple[str, float]]:
    response = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "observation_start": start_date,
            "sort_order": "asc",
        },
        timeout=20,
    )
    response.raise_for_status()
    values: list[tuple[str, float]] = []
    for row in response.json().get("observations", []):
        raw = row.get("value")
        if raw in (None, "."):
            continue
        try:
            values.append((row["date"], float(raw)))
        except (TypeError, ValueError):
            continue
    return values


def collect_global_financial_conditions(lookback_years: int = 3) -> int:
    api_key = _get_api_key()
    if not api_key:
        return 0

    start_date = (datetime.now() - timedelta(days=lookback_years * 365)).strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executemany(
        """
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
        """,
        CATEGORIES,
    )

    total = 0
    for series_id, our_code, _transform in FRED_SERIES:
        try:
            values = _fetch_series(api_key, series_id, start_date)
        except Exception as exc:
            logger.warning("FRED financial series failed [%s]: %s", series_id, exc)
            continue
        values.sort(key=lambda item: item[0])
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
                (our_code, date, value, prev, change_pct),
            )
            total += 1
        time.sleep(0.5)

    conn.commit()
    conn.close()
    _log("ok", total)
    logger.info("Global financial conditions collected %s records", total)
    return total


def _log(status: str, records: int, message: str = "") -> None:
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.execute(
            """
            INSERT INTO global_macro_collection_log (source, status, records, message)
            VALUES ('global_financial', ?, ?, ?)
            """,
            (status, records, message),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    count = collect_global_financial_conditions()
    print(f"글로벌 금융여건 수집 완료: {count}건")
