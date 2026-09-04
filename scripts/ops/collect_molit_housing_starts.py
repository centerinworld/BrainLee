#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "stock.db"
URL = "https://stat.molit.go.kr/portal/main/portalMain.do"
TARGET_KEY = "cafe:11:4247"
DATA_URL = "https://stat.molit.go.kr/portal/stat/data.do"


def collect() -> dict:
    response = requests.get(URL, timeout=30, headers={"User-Agent": "stock-dashboard/1.0"})
    response.raise_for_status()
    match = re.search(r"var chartData = JSON\.parse\('(.+?)'\);", response.text)
    if not match:
        raise RuntimeError("MOLIT chartData not found")
    chart = json.loads(match.group(1))
    rows = chart.get("주택건설실적") or []
    if not rows:
        raise RuntimeError("MOLIT housing construction rows not found")

    latest_period = None
    for row in rows:
        base = str(row.get("baseDt") or "")
        period_match = re.search(r"(\d{4})년(\d{2})월", base)
        if period_match:
            latest_period = f"{period_match.group(1)}{period_match.group(2)}"
            break
    if not latest_period:
        raise RuntimeError("MOLIT latest housing period not found")

    end_year, end_month = int(latest_period[:4]), int(latest_period[4:])
    month_index = end_year * 12 + end_month - 1 - 59
    start_period = f"{month_index // 12:04d}{month_index % 12 + 1:02d}"
    history_response = requests.get(
        DATA_URL,
        params={
            "formId": "5386",
            "styleNum": "1",
            "apprYn": "Y",
            "startDate": start_period,
            "endDate": latest_period,
        },
        timeout=60,
        headers={"User-Agent": "stock-dashboard/1.0"},
    )
    history_response.raise_for_status()
    history_payload = history_response.json()
    if not history_payload.get("result"):
        raise RuntimeError(f"MOLIT history error: {history_payload.get('msg')}")

    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute(
        "DELETE FROM quant_major_indicator_series WHERE indicator_key=? AND series_name='주택건설실적_착공' AND source_name='MOLIT_STAT'",
        (TARGET_KEY,),
    )
    saved = 0
    history_saved = 0
    for row in history_payload.get("data") or []:
        if row.get("1") != "총계" or row.get("2") != "총계" or row.get("3") != "전국":
            continue
        period = str(row.get("0") or "")
        value = row.get("4")
        if not re.fullmatch(r"\d{4}-\d{2}", period) or value in (None, ""):
            continue
        conn.execute(
            """
            INSERT INTO quant_major_indicator_series
                (indicator_key, period, series_name, value, unit, source_name,
                 source_detail, quality, updated_at)
            VALUES (?, ?, '주택건설실적_착공', ?, '호', 'MOLIT_STAT_5386',
                    '국토교통 통계누리 주택건설 착공실적 전국 월계', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(indicator_key, period, series_name, source_name) DO UPDATE SET
                value=excluded.value,
                source_detail=excluded.source_detail,
                quality=excluded.quality,
                updated_at=excluded.updated_at
            """,
            (TARGET_KEY, period, float(str(value).replace(",", "")), "official_provisional" if period >= "2025-01" else "official"),
        )
        history_saved += 1

    for row in rows:
        base = str(row.get("baseDt") or "")
        period_match = re.search(r"(\d{4})년(\d{2})월", base)
        item = str(row.get("itemNm") or "").strip()
        value = row.get("itemVl")
        if not period_match or item not in {"착공", "인허가", "준공", "분양"} or value is None:
            continue
        period = f"{period_match.group(1)}-{period_match.group(2)}"
        conn.execute(
            """
            INSERT INTO quant_major_indicator_series
                (indicator_key, period, series_name, value, unit, source_name,
                 source_detail, quality, updated_at)
            VALUES (?, ?, ?, ?, '호', ?, ?, 'official_latest_snapshot', ?)
            ON CONFLICT(indicator_key, period, series_name, source_name) DO UPDATE SET
                value=excluded.value,
                source_detail=excluded.source_detail,
                quality=excluded.quality,
                updated_at=excluded.updated_at
            """,
            (
                TARGET_KEY,
                period,
                f"주택건설실적_{item}",
                float(value),
                "MOLIT_STAT_5386" if item == "착공" else "MOLIT_STAT",
                f"국토교통 통계누리 메인 공개값 ({base}); 월별 누적 스냅샷",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        saved += 1
    conn.execute(
        """
        UPDATE quant_major_indicator_catalog
        SET status='ready_existing', source_system='국토교통 통계누리',
            notes='공식 전국 월별 착공 시계열을 최근 60개월 갱신. 2025~2026년 값은 잠정치이며 인허가/준공/분양 최신 스냅샷도 함께 보관.',
            updated_at=CURRENT_TIMESTAMP
        WHERE indicator_key=?
        """,
        (TARGET_KEY,),
    )
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) FROM quant_major_indicator_series WHERE indicator_key=?", (TARGET_KEY,)
    ).fetchone()[0]
    conn.close()
    return {"saved": saved, "history_saved": history_saved, "total_rows": count, "source": URL}


if __name__ == "__main__":
    print(json.dumps(collect(), ensure_ascii=False))
