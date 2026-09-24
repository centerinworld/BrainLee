"""HKEX Stock Connect 남향자금(중국 본토 -> 홍콩) 일별 수집기.

홍콩은 투자자 국적별 전체 순매수를 공표하지 않는다. 따라서 이 지표는 홍콩시장
전체의 '외국인 순매수'가 아니라 HKEX가 공표하는 Shanghai/Shenzhen Southbound
매수·매도 금액의 차이다. 화면과 API에서도 이 한계를 명시한다.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta

import requests

from collectors.asia_foreign_flow_collector import _fx_rate_near, _log, _upsert
from db_compat import connect_primary_db

logger = logging.getLogger(__name__)

HKEX_DAILY_URL = "https://www.hkex.com.hk/eng/csm/DailyStat/data_tab_daily_{date}e.js"
_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.hkex.com.hk/Mutual-Market/Stock-Connect/Statistics/Historical-Daily?sc_lang=en",
}


def _number(value) -> float:
    return float(str(value).replace(",", "").strip())


def parse_hkex_southbound_js(payload: str) -> tuple[str | None, float | None]:
    """HKEX JS에서 거래일과 SH+SZ 남향 순매수(HKD 백만)를 반환한다."""
    prefix = "tabData"
    if not payload.lstrip().startswith(prefix):
        return None, None
    body = payload[payload.index("=") + 1 :].strip().rstrip(";").strip()
    try:
        markets = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None, None

    date = None
    net_hkd_million = 0.0
    found = 0
    for market in markets:
        if market.get("market") not in {"SSE Southbound", "SZSE Southbound"}:
            continue
        if not market.get("tradingDay"):
            continue
        content = next((item for item in market.get("content", []) if item.get("style") == 1), None)
        table = (content or {}).get("table", {})
        schema = (table.get("schema") or [[]])[0]
        rows = table.get("tr") or []
        try:
            buy_idx = schema.index("Buy Turnover")
            sell_idx = schema.index("Sell Turnover")
            buy = _number(rows[buy_idx]["td"][0][0])
            sell = _number(rows[sell_idx]["td"][0][0])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        date = str(market.get("date") or date)[:10]
        net_hkd_million += buy - sell
        found += 1

    return (date, net_hkd_million) if found == 2 else (None, None)


def _fetch_hkex_day(day: datetime) -> tuple[str | None, float | None]:
    response = requests.get(
        HKEX_DAILY_URL.format(date=day.strftime("%Y%m%d")),
        headers=_HEADERS,
        timeout=20,
    )
    if response.status_code == 404:
        return None, None
    response.raise_for_status()
    return parse_hkex_southbound_js(response.text)


def collect_hk_southbound_flow(days: int = 120) -> int:
    """증분 수집 후 HK_MAINLAND_SOUTHBOUND_FLOW_USD에 USD 백만 단위로 저장한다."""
    conn = connect_primary_db(timeout=30)
    try:
        latest = conn.execute(
            "SELECT MAX(date) FROM global_macro_data "
            "WHERE indicator_code='HK_MAINLAND_SOUTHBOUND_FLOW_USD'"
        ).fetchone()[0]
        today = datetime.now()
        start = max(
            today - timedelta(days=max(1, days)),
            datetime.strptime(str(latest)[:10], "%Y-%m-%d") + timedelta(days=1) if latest else datetime.min,
        )
        prev_row = conn.execute(
            "SELECT value FROM global_macro_data "
            "WHERE indicator_code='HK_MAINLAND_SOUTHBOUND_FLOW_USD' ORDER BY date DESC LIMIT 1"
        ).fetchone()
        prev = float(prev_row[0]) if prev_row else None
        total = 0
        cursor = start
        while cursor.date() <= today.date():
            if cursor.weekday() < 5:
                date, net_hkd_million = _fetch_hkex_day(cursor)
                if date and net_hkd_million is not None:
                    fx = _fx_rate_near(conn, "HK_USD_HKD", date)
                    if fx:
                        usd_million = net_hkd_million / fx
                        _upsert(conn, "HK_MAINLAND_SOUTHBOUND_FLOW_USD", date, usd_million, prev)
                        prev = usd_million
                        total += 1
                time.sleep(0.15)
            cursor += timedelta(days=1)

        _log(conn, "hk_southbound_flow", "ok", total, "HKEX SH+SZ Southbound buy minus sell; HKD million converted to USD")
        conn.commit()
        logger.info("HK southbound flow collected %s records", total)
        return total
    except Exception as exc:
        logger.warning("HK southbound flow failed: %s", exc)
        _log(conn, "hk_southbound_flow", "error", 0, str(exc))
        conn.commit()
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(collect_hk_southbound_flow())
