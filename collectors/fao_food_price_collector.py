"""
FAO Food Price Index collector.

Downloads the official FAO monthly nominal CSV and stores the headline index
plus the five commodity sub-indices in global_macro_data.
"""
from __future__ import annotations

import csv
import html
import io
import logging
import re
import sqlite3
import urllib.request

DB_PATH = "stock.db"
PAGE_URL = "https://www.fao.org/worldfoodsituation/foodpricesindex/en/"
logger = logging.getLogger(__name__)

COLUMN_MAP = {
    "Food Price Index": "GLOBAL_FOOD_PRICE",
    "Meat": "GLOBAL_FOOD_MEAT",
    "Dairy": "GLOBAL_FOOD_DAIRY",
    "Cereals": "GLOBAL_FOOD_CEREALS",
    "Oils": "GLOBAL_FOOD_OILS",
    "Sugar": "GLOBAL_FOOD_SUGAR",
}


def _fetch(url: str, referer: str | None = None) -> bytes:
    headers = {"User-Agent": "Mozilla/5.0"}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _official_csv_url() -> str:
    html_text = _fetch(PAGE_URL).decode("utf-8", errors="ignore")
    match = re.search(r'href="([^"]*food_price_indices_data\.csv[^"]*)"', html_text, re.I)
    if not match:
        raise RuntimeError("FAO food price CSV link not found")
    return html.unescape(match.group(1))


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    value = str(value).strip().replace(",", "")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def collect_fao_food_prices() -> int:
    url = _official_csv_url()
    raw = _fetch(url, referer=PAGE_URL).decode("utf-8-sig", errors="ignore")
    reader = csv.reader(io.StringIO(raw))
    rows = list(reader)
    header_idx = next(i for i, row in enumerate(rows) if row and row[0] == "Date")
    headers = rows[header_idx]
    col_indexes = {
        COLUMN_MAP[name]: idx
        for idx, name in enumerate(headers)
        if name in COLUMN_MAP
    }

    conn = sqlite3.connect(DB_PATH, timeout=30)
    total = 0
    last_values: dict[str, float] = {}
    for row in rows[header_idx + 1:]:
        if not row or not row[0] or not re.match(r"^\d{4}-\d{2}$", row[0]):
            continue
        date = f"{row[0]}-01"
        for code, idx in col_indexes.items():
            value = _to_float(row[idx] if idx < len(row) else None)
            if value is None:
                continue
            prev = last_values.get(code)
            change_pct = ((value - prev) / abs(prev) * 100.0) if prev else None
            conn.execute("""
                INSERT INTO global_macro_data (indicator_code, date, value, prev_value, change_pct)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(indicator_code, date) DO UPDATE SET
                    value=excluded.value,
                    prev_value=excluded.prev_value,
                    change_pct=excluded.change_pct
            """, (code, date, value, prev, change_pct))
            last_values[code] = value
            total += 1

    conn.execute("""
        INSERT INTO global_macro_collection_log (source, status, records, message)
        VALUES ('fao_food', ?, ?, ?)
    """, ("ok", total, "official FAO food price index CSV"))
    conn.commit()
    conn.close()
    logger.info("FAO food price index collected %s records", total)
    return total


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    n = collect_fao_food_prices()
    print(f"FAO food price index collected {n} records")
