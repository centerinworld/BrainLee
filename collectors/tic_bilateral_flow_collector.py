"""
미국 재무부 TIC(Treasury International Capital) 기반 국가별 대미 주식 양자간 자금흐름

FRED가 TIC 데이터를 그대로 미러링해 국가별 "Foreign Net Transactions of U.S. Equity
Securities"(해당 국가 투자자의 미국 주식 순매수, 백만달러/월) 시계열을 제공한다.
기존 KRX/NSDL 기반 수집기(asia_foreign_flow_collector.py 등)가 "그 나라 자국 시장에
외국인 돈이 들어왔는가"를 보는 반면, 이 수집기는 "그 나라 투자자 돈이 미국 주식으로
들어왔는가"를 직접 측정한다 — "아시아 이탈 자금이 미국으로 가는지" 가설을 검증하는
핵심 데이터.

collectors/fred_collector.py와 동일한 FRED API(_FRED_API_KEY, 이미 .env에 있음)를
재사용하되, 별도 목록/코드 네임스페이스로 분리해 global_foreign_flow 전용으로 관리한다.
단위: 백만달러(Millions of Dollars), 월별, 공식 발표 특성상 약 2~3개월 지연.
"""
from __future__ import annotations

from db_compat import connect_primary_db
import logging
import os
from datetime import datetime, timedelta

import requests

logger = logging.getLogger(__name__)

# (FRED series id, our_code, label_ko)
# our_code 네임스페이스: TIC_US_FLOW_<국가/지역코드> — "해당 국가→미국 주식 순매수"
TIC_SERIES = [
    ("FORLTEQTYNET69995", "TIC_US_FLOW_ALL",          "전세계→미국 합계"),
    ("FORLTEQTYNET49999", "TIC_US_FLOW_ASIA_TOTAL",   "아시아 합계→미국"),
    ("FORLTEQTYNET19992", "TIC_US_FLOW_EUROPE_TOTAL", "유럽 합계→미국"),
    ("FORLTEQTYNET16713", "TIC_US_FLOW_EURO_AREA",    "유로존→미국"),
    ("FORLTEQTYNET42609", "TIC_US_FLOW_JP",            "일본→미국"),
    ("FORLTEQTYNET43001", "TIC_US_FLOW_KR",            "한국→미국"),
    ("FORLTEQTYNET41408", "TIC_US_FLOW_CN",            "중국(본토)→미국"),
    ("FORLTEQTYNET42005", "TIC_US_FLOW_HK",            "홍콩→미국"),
    ("FORLTEQTYNET46302", "TIC_US_FLOW_TW",            "대만→미국"),
    ("FORLTEQTYNET42102", "TIC_US_FLOW_IN",            "인도→미국"),
    ("FORLTEQTYNET46019", "TIC_US_FLOW_SG",            "싱가포르→미국"),
    ("FORLTEQTYNET13005", "TIC_US_FLOW_GB",            "영국→미국"),
    ("FORLTEQTYNET11002", "TIC_US_FLOW_DE",            "독일→미국"),
    ("FORLTEQTYNET10804", "TIC_US_FLOW_FR",            "프랑스→미국"),
    ("FORLTEQTYNET11509", "TIC_US_FLOW_IT",            "이탈리아→미국"),
    ("FORLTEQTYNET12106", "TIC_US_FLOW_NL",            "네덜란드→미국"),
    ("FORLTEQTYNET12505", "TIC_US_FLOW_ES",            "스페인→미국"),
    ("FORLTEQTYNET12602", "TIC_US_FLOW_SE",            "스웨덴→미국"),
    ("FORLTEQTYNET12688", "TIC_US_FLOW_CH",            "스위스→미국"),
    ("FORLTEQTYNET29998", "TIC_US_FLOW_CA",            "캐나다→미국"),
    ("FORLTEQTYNET60089", "TIC_US_FLOW_AU",            "호주→미국"),
    ("FORLTEQTYNET45608", "TIC_US_FLOW_SA",            "사우디아라비아→미국"),
    ("FORLTEQTYNET46604", "TIC_US_FLOW_AE",            "아랍에미리트→미국"),
    ("FORLTEQTYNET55719", "TIC_US_FLOW_ZA",            "남아프리카공화국→미국"),
    ("FORLTEQTYNET30309", "TIC_US_FLOW_BR",            "브라질→미국"),
]


def _get_api_key() -> str | None:
    key = os.getenv("FRED_API_KEY", "")
    if not key:
        logger.warning("FRED_API_KEY not set — skipping TIC bilateral flow collection.")
    return key or None


def _fetch(api_key: str, series_id: str, start_date: str) -> list[tuple]:
    try:
        resp = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": series_id, "api_key": api_key, "file_type": "json",
                "observation_start": start_date, "sort_order": "asc",
            },
            timeout=15,
        )
        resp.raise_for_status()
        out = []
        for o in resp.json().get("observations", []):
            v = o.get("value", ".")
            if v == ".":
                continue
            out.append((o["date"], float(v)))
        return out
    except Exception as e:
        logger.warning("TIC fetch failed [%s]: %s", series_id, e)
        return []


def collect_tic_bilateral_flow(lookback_years: int = 5) -> int:
    api_key = _get_api_key()
    if not api_key:
        return 0

    start_date = (datetime.now() - timedelta(days=lookback_years * 365)).strftime("%Y-%m-%d")
    conn = connect_primary_db(timeout=30)
    total = 0
    try:
        for series_id, our_code, _label in TIC_SERIES:
            values = _fetch(api_key, series_id, start_date)
            prev = None
            for date, val in values:
                chg = ((val - prev) / abs(prev) * 100.0) if prev else None
                conn.execute(
                    """
                    INSERT INTO global_macro_data (indicator_code, date, value, prev_value, change_pct)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(indicator_code, date) DO UPDATE SET
                        value=excluded.value, prev_value=excluded.prev_value, change_pct=excluded.change_pct
                    """,
                    (our_code, date, val, prev, chg),
                )
                prev = val
                total += 1
        conn.execute(
            "INSERT INTO global_macro_collection_log (source, status, records, message) "
            "VALUES ('tic_bilateral_flow', 'ok', ?, 'FRED-mirrored TIC, monthly, 25 country/region series')",
            (total,),
        )
        conn.commit()
        logger.info("TIC bilateral flow collected %s records", total)
        return total
    except Exception as e:
        logger.warning("TIC bilateral flow failed: %s", e)
        conn.execute(
            "INSERT INTO global_macro_collection_log (source, status, records, message) VALUES ('tic_bilateral_flow', 'error', 0, ?)",
            (str(e),),
        )
        conn.commit()
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(collect_tic_bilateral_flow())
