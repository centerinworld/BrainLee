"""
EIA oil supply collector.

Downloads official EIA weekly petroleum history spreadsheets and stores crude
oil inventory series in global_macro_data.
"""
from __future__ import annotations

import logging
import sqlite3
import tempfile
import urllib.request

import pandas as pd

DB_PATH = "stock.db"
logger = logging.getLogger(__name__)

SERIES = [
    ("WCESTUS1", "OIL_STOCKS_EX_SPR", "Weekly U.S. ending stocks excluding SPR of crude oil"),
    ("WCRSTUS1", "OIL_STOCKS_TOTAL", "Weekly U.S. ending stocks of crude oil"),
]


def _download_xls(source_key: str) -> str:
    url = f"https://www.eia.gov/dnav/pet/hist_xls/{source_key.lower()}w.xls"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        raw = resp.read()
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xls")
    tmp.write(raw)
    tmp.close()
    return tmp.name


def _load_rows(source_key: str) -> list[tuple[str, float]]:
    path = _download_xls(source_key)
    df = pd.read_excel(path, sheet_name="Data 1", header=None)
    rows: list[tuple[str, float]] = []
    for _, row in df.iloc[3:].iterrows():
        date_val = row.iloc[0]
        value = row.iloc[1]
        if pd.isna(date_val) or pd.isna(value):
            continue
        date = date_val.strftime("%Y-%m-%d") if hasattr(date_val, "strftime") else str(date_val)[:10]
        rows.append((date, float(value)))
    rows.sort(key=lambda x: x[0])
    return rows


def collect_eia_oil_supply() -> int:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    total = 0
    for source_key, code, _label in SERIES:
        rows = _load_rows(source_key)
        prev = None
        for date, value in rows:
            change_pct = ((value - prev) / abs(prev) * 100.0) if prev else None
            conn.execute("""
                INSERT INTO global_macro_data (indicator_code, date, value, prev_value, change_pct)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(indicator_code, date) DO UPDATE SET
                    value=excluded.value,
                    prev_value=excluded.prev_value,
                    change_pct=excluded.change_pct
            """, (code, date, value, prev, change_pct))
            prev = value
            total += 1

    conn.execute("""
        INSERT INTO global_macro_collection_log (source, status, records, message)
        VALUES ('eia_oil', ?, ?, ?)
    """, ("ok", total, "official EIA weekly crude oil stock spreadsheets"))
    conn.commit()
    conn.close()
    logger.info("EIA oil supply collected %s records", total)
    return total


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    n = collect_eia_oil_supply()
    print(f"EIA oil supply collected {n} records")
