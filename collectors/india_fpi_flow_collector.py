"""
인도 FPI(외국인포트폴리오투자자) 자금흐름 수집기

NSDL(예탁결제기구) 공개 리포트 페이지에서 "당일 주식(Equity) 순투자" 값을 그대로 가져온다.
  https://www.fpi.nsdl.co.in/Reports/Latest.aspx
이 페이지는 이미 "Net Investment US($) million" 컬럼을 직접 제공하므로 별도 환율 환산이 불필요.

⚠️ 2026-09-08 조사 메모: WebFetch 도구로는 이 사이트가 ECONNRESET으로 실패했지만,
실제 운영 서버(이 Mac)의 네트워크로는 정상 200 응답을 확인함 — 반대로 대만 TWSE는
이 Mac에서 WAF(307 보안차단)에 막힘. 도구별로 접근성이 다를 수 있어 반드시 실제
운영 환경에서 재검증 후 반영함(routes/global_foreign_flow.py의 data_confidence 참조).

nseindia.com(FII/DII 대체 소스)은 403으로 차단 확인 — 이 수집기는 NSDL만 사용한다.
"""
from __future__ import annotations

from db_compat import connect_primary_db
import logging
import re
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

URL = "https://www.fpi.nsdl.co.in/Reports/Latest.aspx"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


def _parse_num(s: str) -> float | None:
    s = (s or "").strip().replace(",", "")
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        v = float(s)
        return -v if neg else v
    except ValueError:
        return None


def _fetch_latest() -> tuple[str, float] | None:
    """반환: (ISO 날짜, 주식(Equity) 순투자 USD 백만달러). 실패 시 None."""
    resp = requests.get(URL, headers=_HEADERS, timeout=20)
    resp.raise_for_status()
    html = resp.text

    m = re.search(r"Daily Trends in FPI Investments on\s*([\d]{1,2}-[A-Za-z]{3}-\d{4})", html)
    if not m:
        return None
    date_iso = datetime.strptime(m.group(1), "%d-%b-%Y").strftime("%Y-%m-%d")

    tables = re.findall(r"<table[^>]*>.*?</table>", html, re.S | re.I)
    for table in tables:
        if "Equity" not in table or "Net Investment" not in table:
            continue
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S | re.I)
        for row in rows:
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S | re.I)
            cells = [re.sub("<.*?>", "", c).strip() for c in cells]
            if cells and cells[0].strip().lower() == "sub-total":
                # 리포트 구조상 Equity 카테고리가 항상 첫 번째 Sub-total
                net_usd = _parse_num(cells[-1])
                if net_usd is not None:
                    return date_iso, net_usd
    return None


def collect_india_fpi_flow() -> int:
    conn = connect_primary_db(timeout=30)
    try:
        result = _fetch_latest()
        if result is None:
            conn.execute(
                "INSERT INTO global_macro_collection_log (source, status, records, message) "
                "VALUES ('india_fpi_flow', 'error', 0, 'parse failed or site unreachable')"
            )
            conn.commit()
            logger.warning("India FPI flow: no data parsed")
            return 0
        date_iso, net_usd = result
        prev_row = conn.execute(
            "SELECT value FROM global_macro_data WHERE indicator_code='IN_FPI_FLOW_USD' "
            "AND date<? ORDER BY date DESC LIMIT 1",
            (date_iso,),
        ).fetchone()
        prev = float(prev_row[0]) if prev_row else None
        change_pct = ((net_usd - prev) / abs(prev) * 100.0) if prev else None
        conn.execute(
            """
            INSERT INTO global_macro_data (indicator_code, date, value, prev_value, change_pct)
            VALUES ('IN_FPI_FLOW_USD', ?, ?, ?, ?)
            ON CONFLICT(indicator_code, date) DO UPDATE SET
                value=excluded.value, prev_value=excluded.prev_value, change_pct=excluded.change_pct
            """,
            (date_iso, net_usd, prev, change_pct),
        )
        conn.execute(
            "INSERT INTO global_macro_collection_log (source, status, records, message) "
            "VALUES ('india_fpi_flow', 'ok', 1, ?)",
            (f"NSDL Latest.aspx {date_iso} = {net_usd}",),
        )
        conn.commit()
        logger.info("India FPI flow collected: %s = %.1f USD million", date_iso, net_usd)
        return 1
    except Exception as e:
        logger.warning("India FPI flow failed: %s", e)
        conn.execute(
            "INSERT INTO global_macro_collection_log (source, status, records, message) VALUES ('india_fpi_flow', 'error', 0, ?)",
            (str(e),),
        )
        conn.commit()
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(collect_india_fpi_flow())
