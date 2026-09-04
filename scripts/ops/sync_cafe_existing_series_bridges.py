#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "stock.db"

SERIES_BRIDGES = {
    "cafe:11:2650": ["epic:10:11"],
    # Company totals and country totals only. Model-level rows would crowd the
    # traffic-light dashboard and do not represent the theme-level signal.
    "cafe:11:2805": ["epic:0:1", "epic:0:2"],
    "cafe:34:7690": ["epic:2:93", "epic:2:96", "epic:2:98", "epic:3:70"],
    "cafe:11:4128": ["public:23:4", "epic:semi:dram_proxy"],
    "cafe:34:7611": ["epic:10:11"],
    "cafe:34:7633": ["epic:11:105"],
}

BRIDGE_STATUSES = {
    "cafe:11:4128": "partial_existing",
    "cafe:34:7611": "partial_existing",
    "cafe:34:7633": "partial_existing",
}


def sync_copied_series(conn: sqlite3.Connection, target_key: str, source_keys: list[str]) -> int:
    placeholders = ",".join("?" for _ in source_keys)
    before = conn.total_changes
    conn.execute(
        f"""
        INSERT INTO quant_major_indicator_series
            (indicator_key, period, series_name, value, unit, source_name,
             source_detail, quality, updated_at)
        SELECT ?, period, source_name || ':' || series_name, value, unit, source_name,
               '카페 지표 연계: ' || source_detail, quality, CURRENT_TIMESTAMP
        FROM quant_major_indicator_series
        WHERE indicator_key IN ({placeholders}) AND value IS NOT NULL
        ON CONFLICT(indicator_key, period, series_name, source_name) DO UPDATE SET
            value=excluded.value,
            unit=excluded.unit,
            source_detail=excluded.source_detail,
            quality=excluded.quality,
            updated_at=excluded.updated_at
        """,
        [target_key, *source_keys],
    )
    return conn.total_changes - before


def sync_construction_series(conn: sqlite3.Connection) -> int:
    before = conn.total_changes
    conn.execute(
        """
        INSERT INTO quant_major_indicator_series
            (indicator_key, period, series_name, value, unit, source_name,
             source_detail, quality, updated_at)
        SELECT 'cafe:11:3475', SUBSTR(date, 1, 7),
               CASE indicator_code
                 WHEN 'KR_CONSTRUCTION_ORDER' THEN '건설수주액_실질'
                 ELSE '건설기성액_실질'
               END,
               value, '십억원', 'KOSIS_DT_1C8016',
               '통계청 경기종합지수 구성지표', 'official', CURRENT_TIMESTAMP
        FROM global_macro_data
        WHERE indicator_code IN ('KR_CONSTRUCTION_ORDER', 'KR_CONSTRUCTION_OUTPUT')
          AND value IS NOT NULL
        ON CONFLICT(indicator_key, period, series_name, source_name) DO UPDATE SET
            value=excluded.value,
            unit=excluded.unit,
            source_detail=excluded.source_detail,
            quality=excluded.quality,
            updated_at=excluded.updated_at
        """
    )
    return conn.total_changes - before


def sync_air_fuel_proxy(conn: sqlite3.Connection) -> int:
    before = conn.total_changes
    conn.execute(
        """
        INSERT INTO quant_major_indicator_series
            (indicator_key, period, series_name, value, unit, source_name,
             source_detail, quality, updated_at)
        SELECT 'cafe:34:7616', SUBSTR(date, 1, 7),
               CASE indicator_code
                 WHEN 'COMM_OIL_BRENT' THEN '항공유_원가_브렌트유_proxy'
                 ELSE '항공유_원가_WTI_proxy'
               END,
               AVG(value), 'USD/bbl', 'YAHOO_OIL_PROXY',
               '항공유 직접 가격이 아닌 브렌트/WTI 월평균 원가 대리지표',
               'proxy_monthly', CURRENT_TIMESTAMP
        FROM global_macro_data
        WHERE indicator_code IN ('COMM_OIL_BRENT', 'COMM_OIL_WTI')
          AND value IS NOT NULL
        GROUP BY indicator_code, SUBSTR(date, 1, 7)
        ON CONFLICT(indicator_key, period, series_name, source_name) DO UPDATE SET
            value=excluded.value,
            source_detail=excluded.source_detail,
            quality=excluded.quality,
            updated_at=excluded.updated_at
        """
    )
    return conn.total_changes - before


def mark_status(conn: sqlite3.Connection, statuses: dict[str, str]) -> None:
    note = "기존 공식 시계열을 카페 주제 지표로 연결. 세부 구성 시계열별 품질 표기 참조."
    for key, status in statuses.items():
        conn.execute(
            """
            UPDATE quant_major_indicator_catalog
            SET status=?,
                notes=CASE WHEN INSTR(COALESCE(notes,''), ?) > 0 THEN notes
                           ELSE COALESCE(notes || ' / ', '') || ? END,
                updated_at=?
            WHERE indicator_key=?
            """,
            (status, note, note, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), key),
        )


def main() -> None:
    conn = sqlite3.connect(DB_PATH, timeout=60)
    for target_key in [*SERIES_BRIDGES, "cafe:11:3475", "cafe:34:7616"]:
        conn.execute("DELETE FROM quant_major_indicator_series WHERE indicator_key=?", (target_key,))
    copied = {key: sync_copied_series(conn, key, sources) for key, sources in SERIES_BRIDGES.items()}
    copied["cafe:11:3475"] = sync_construction_series(conn)
    copied["cafe:34:7616"] = sync_air_fuel_proxy(conn)
    mark_status(
        conn,
        {
            **{key: BRIDGE_STATUSES.get(key, "ready_existing") for key in SERIES_BRIDGES},
            "cafe:11:3475": "ready_existing",
            "cafe:34:7616": "partial_existing",
        },
    )
    conn.commit()
    counts = {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT indicator_key, COUNT(*) FROM quant_major_indicator_series "
            "WHERE indicator_key IN (%s) GROUP BY indicator_key"
            % ",".join("?" for _ in copied),
            list(copied),
        )
    }
    conn.close()
    print(json.dumps({"upsert_changes": copied, "series_rows": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
