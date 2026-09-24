"""ECB 월별 유로존 외부 포트폴리오 주식자금 유입 수집기.

양(+)의 값은 비거주자가 보유하는 유로존의 주식 및 투자펀드 지분 부채가 거래로
순증가했음을 뜻한다. 상장주식만의 거래량은 아니므로 화면에 정의를 함께 표시한다.
"""
from __future__ import annotations

import csv
import io
import logging

import requests

from collectors.asia_foreign_flow_collector import _fx_rate_near, _log, _upsert
from db_compat import connect_primary_db

logger = logging.getLogger(__name__)

ECB_SERIES = "BPS.M.N.I10.W1.S1.S1.T.L.FA.P.F5._Z.EUR._T.M.N.ALL"
ECB_API_URL = f"https://data-api.ecb.europa.eu/service/data/BPS/{ECB_SERIES.removeprefix('BPS.')}"


def parse_ecb_csv(payload: str) -> list[tuple[str, float]]:
    """ECB CSV를 (월초 ISO 날짜, 백만 유로)로 변환한다."""
    rows = []
    for row in csv.DictReader(io.StringIO(payload)):
        period = str(row.get("TIME_PERIOD") or "")[:7]
        value = row.get("OBS_VALUE")
        if len(period) != 7 or value in (None, ""):
            continue
        try:
            rows.append((f"{period}-01", float(value)))
        except (TypeError, ValueError):
            continue
    return sorted(rows)


def collect_ecb_euro_equity_flow(start_period: str = "2021-01") -> int:
    """유로존 주식·펀드지분 외부 유입을 USD 백만 단위로 증분 갱신한다."""
    conn = connect_primary_db(timeout=30)
    try:
        response = requests.get(
            ECB_API_URL,
            params={"startPeriod": start_period, "format": "csvdata"},
            headers={"Accept": "text/csv", "User-Agent": "stock-dashboard/1.0"},
            timeout=30,
        )
        response.raise_for_status()
        values = parse_ecb_csv(response.text)
        prev = None
        total = 0
        for date, eur_million in values:
            eur_usd = _fx_rate_near(conn, "EU_EUR_USD", date)
            if not eur_usd:
                continue
            usd_million = eur_million * eur_usd
            _upsert(conn, "EURO_AREA_EQUITY_FUND_INFLOW_USD", date, usd_million, prev)
            prev = usd_million
            total += 1
        _log(
            conn,
            "ecb_euro_equity_flow",
            "ok",
            total,
            "ECB BPS monthly portfolio equity and investment-fund-share liabilities; EUR converted to USD",
        )
        conn.commit()
        logger.info("ECB euro equity flow collected %s records", total)
        return total
    except Exception as exc:
        logger.warning("ECB euro equity flow failed: %s", exc)
        _log(conn, "ecb_euro_equity_flow", "error", 0, str(exc))
        conn.commit()
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(collect_ecb_euro_equity_flow())
