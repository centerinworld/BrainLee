"""
DRAM spot price collector.

Source: TrendForce / DRAMeXchange public DRAM Spot Price table.
The collector stores actual spot-market session averages, not export-unit-value
proxies. It also mirrors the rows into quant_major_indicator_series so the
stock_dashboard quant catalog and Global Intelligence use the same source.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)
DB_PATH = "stock.db"
SOURCE_URL = "https://www.trendforce.com/price/dram/dram_spot"


@dataclass(frozen=True)
class DramSpotSpec:
    code: str
    quant_key: str
    item: str
    name: str
    name_en: str
    importance: int


SPECS = [
    DramSpotSpec("MQ_DRAM_SPOT_DDR5_16GB_4800", "market:dram:spot:ddr5_16gb_4800", "DDR5 16Gb (2Gx8) 4800/5600", "DDR5 16Gb 4800/5600 현물가", "DDR5 16Gb 4800/5600 DRAM Spot Price", 3),
    DramSpotSpec("MQ_DRAM_SPOT_DDR5_16GB_ETT", "market:dram:spot:ddr5_16gb_ett", "DDR5 16Gb (2Gx8) eTT", "DDR5 16Gb eTT 현물가", "DDR5 16Gb eTT DRAM Spot Price", 2),
    DramSpotSpec("MQ_DRAM_SPOT_DDR4_16GB_3200", "market:dram:spot:ddr4_16gb_3200", "DDR4 16Gb (2Gx8) 3200", "DDR4 16Gb 3200 현물가", "DDR4 16Gb 3200 DRAM Spot Price", 3),
    DramSpotSpec("MQ_DRAM_SPOT_DDR4_8GB_3200", "market:dram:spot:ddr4_8gb_3200", "DDR4 8Gb (1Gx8) 3200", "DDR4 8Gb 3200 현물가", "DDR4 8Gb 3200 DRAM Spot Price", 3),
    DramSpotSpec("MQ_DRAM_SPOT_DDR4_8GB_ETT", "market:dram:spot:ddr4_8gb_ett", "DDR4 8Gb (1Gx8) eTT", "DDR4 8Gb eTT 현물가", "DDR4 8Gb eTT DRAM Spot Price", 2),
    DramSpotSpec("MQ_DRAM_SPOT_DDR3_4GB_1600", "market:dram:spot:ddr3_4gb_1600", "DDR3 4Gb 512Mx8 1600/1866", "DDR3 4Gb 1600/1866 현물가", "DDR3 4Gb 1600/1866 DRAM Spot Price", 1),
]


def _to_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(str(raw).replace(",", "").strip())
    except ValueError:
        return None


def _parse_update_date(text: str) -> str:
    match = re.search(r"Last Update\s+(\d{4}-\d{2}-\d{2})", text)
    if match:
        return match.group(1)
    return datetime.now().strftime("%Y-%m-%d")


def _fetch_spot_rows() -> tuple[str, list[dict]]:
    response = requests.get(
        SOURCE_URL,
        timeout=25,
        headers={"User-Agent": "Mozilla/5.0 (stock-dashboard DRAM spot collector)"},
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    update_date = _parse_update_date(soup.get_text(" ", strip=True))

    rows = []
    for spec in SPECS:
        node = soup.find(string=lambda s, item=spec.item: bool(s and item in s))
        if not node:
            logger.warning("DRAM spot item not found: %s", spec.item)
            continue
        tr = node.find_parent("tr")
        if not tr:
            continue
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(cells) < 6:
            continue
        daily_high = _to_float(cells[1])
        daily_low = _to_float(cells[2])
        session_high = _to_float(cells[3])
        session_low = _to_float(cells[4])
        session_avg = _to_float(cells[5])
        change_pct = _to_float(cells[7] if len(cells) > 7 else None)
        if session_avg is None:
            continue
        rows.append({
            "spec": spec,
            "date": update_date,
            "value": session_avg,
            "daily_high": daily_high,
            "daily_low": daily_low,
            "session_high": session_high,
            "session_low": session_low,
            "change_pct": change_pct,
        })
    return update_date, rows


def collect_dram_spot() -> int:
    update_date, rows = _fetch_spot_rows()
    if not rows:
        _log("warning", 0, "TrendForce DRAM spot table returned no rows")
        return 0

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
        [
            (
                row["spec"].code,
                row["spec"].name,
                row["spec"].name_en,
                "MARKET_QUANT",
                "DRAM_SPOT",
                "USD",
                "TrendForce/DRAMeXchange",
                row["spec"].item,
                "DAILY",
                row["spec"].importance,
            )
            for row in rows
        ],
    )
    conn.executemany(
        """
        INSERT INTO quant_major_indicator_catalog
        (indicator_key, epic_indicator_name, frequency, base_unit, status,
         replacement_family, source_system, collector_path, exactness, priority, notes, enabled)
        VALUES (?, ?, 'Daily', 'USD', 'ready_existing', 'dram_spot',
                'TrendForce/DRAMeXchange', 'collectors/dram_spot_collector.py',
                'public_spot_session_average', 'P0',
                'Actual DRAM spot-market session average from TrendForce/DRAMeXchange public table; not export-unit-value proxy.',
                1)
        ON CONFLICT(indicator_key) DO UPDATE SET
            epic_indicator_name=excluded.epic_indicator_name,
            frequency=excluded.frequency,
            base_unit=excluded.base_unit,
            status=excluded.status,
            replacement_family=excluded.replacement_family,
            source_system=excluded.source_system,
            collector_path=excluded.collector_path,
            exactness=excluded.exactness,
            priority=excluded.priority,
            notes=excluded.notes,
            enabled=excluded.enabled,
            updated_at=CURRENT_TIMESTAMP
        """,
        [(row["spec"].quant_key, row["spec"].name, ) for row in rows],
    )

    total = 0
    for row in rows:
        spec = row["spec"]
        prev = conn.execute(
            """
            SELECT value FROM global_macro_data
            WHERE indicator_code = ? AND date < ?
            ORDER BY date DESC LIMIT 1
            """,
            (spec.code, row["date"]),
        ).fetchone()
        prev_value = float(prev[0]) if prev and prev[0] is not None else None
        change_pct = row["change_pct"]
        if change_pct is None and prev_value:
            change_pct = (row["value"] - prev_value) / abs(prev_value) * 100.0
        conn.execute(
            """
            INSERT INTO global_macro_data (indicator_code, date, value, prev_value, change_pct)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(indicator_code, date) DO UPDATE SET
                value=excluded.value,
                prev_value=excluded.prev_value,
                change_pct=excluded.change_pct
            """,
            (spec.code, row["date"], row["value"], prev_value, change_pct),
        )
        conn.execute(
            """
            INSERT INTO quant_major_indicator_series
            (indicator_key, period, series_name, value, unit, source_name, source_detail, quality)
            VALUES (?, ?, ?, ?, 'USD', 'TrendForce/DRAMeXchange', ?, 'actual_spot_session_average')
            ON CONFLICT(indicator_key, period, series_name, source_name) DO UPDATE SET
                value=excluded.value,
                unit=excluded.unit,
                source_detail=excluded.source_detail,
                quality=excluded.quality,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                spec.quant_key,
                row["date"],
                spec.item,
                row["value"],
                f"{SOURCE_URL}; daily_high={row['daily_high']}; daily_low={row['daily_low']}; session_high={row['session_high']}; session_low={row['session_low']}; update_date={update_date}",
            ),
        )
        total += 1

    conn.execute(
        """
        INSERT INTO global_macro_collection_log (source, status, records, message)
        VALUES ('dram_spot', 'ok', ?, ?)
        """,
        (total, f"TrendForce DRAM spot {update_date}"),
    )
    conn.commit()
    conn.close()
    logger.info("DRAM spot collected %s records for %s", total, update_date)
    return total


def _log(status: str, records: int, message: str = "") -> None:
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.execute(
            """
            INSERT INTO global_macro_collection_log (source, status, records, message)
            VALUES ('dram_spot', ?, ?, ?)
            """,
            (status, records, message),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    count = collect_dram_spot()
    print(f"DRAM 현물가 수집 완료: {count}건")
