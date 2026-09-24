"""
일본 외국인 주식 순매수(대내증권투자, 재무성 MOF 주간 통계) 수집기

⚠️ 2026-09-09 설계 변경: 최초 버전은 e-Stat API 경유를 시도했으나, 실제 사용자가
발급받은 ESTAT_APP_ID로 조회해본 결과 이 통계("対外及び対内証券売買契約等の状況")는
e-Stat 포털에 등록되어 있지 않음(검색 0건, "人口" 등 일반 키워드는 정상 동작 확인
— API 자체는 문제 없었음). 대신 재무성이 이 데이터를 자체 사이트에 **인증 없는 CSV로
직접 공개**하고 있는 것을 확인, 그쪽으로 전환:
  https://www.mof.go.jp/policy/international_policy/reference/itn_transactions_in_securities/week.csv
2005년 1월부터 현재까지 주간 데이터 전체, 최신 업데이트 기준 지연 약 1~2주 — 기존
KR/TW/IN 수집기보다도 접근성이 좋고 히스토리도 훨씬 길다.

대내증권투자(비거주자=외국인의 일본 증권 취득·처분) 중 "주식·투자펀드지분" 순매수만
사용 — 채권(중장기채/단기채)은 미사용. 단위: 억엔(1e8엔) → USD 백만달러 환산.
"""
from __future__ import annotations

from db_compat import connect_primary_db
import logging
import re
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

CSV_URL = "https://www.mof.go.jp/policy/international_policy/reference/itn_transactions_in_securities/week.csv"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

# CSV 컬럼 인덱스(0-based) — 헤더 구조 실측 확인(2026-09-09):
# 0=기간, 12=対内証券投資 주식 취득, 13=처분, 14=주식 순매수(ネット) ← 이걸 사용
_EQUITY_NET_COL = 14

_PERIOD_RE = re.compile(r"(\d{4})．(\d{1,2})．(\d{1,2})〜\s*(\d{1,2})．(\d{1,2})")


def _fx_rate_near(conn, date: str) -> float | None:
    row = conn.execute(
        "SELECT value FROM global_macro_data WHERE indicator_code='JP_USD_JPY' AND date<=? "
        "ORDER BY date DESC LIMIT 1",
        (date,),
    ).fetchone()
    return float(row[0]) if row and row[0] else None


def _parse_period_end_date(period_str: str) -> str | None:
    """'2026．7．26〜8．1' → 종료일(연도 롤오버 처리) ISO 문자열."""
    m = _PERIOD_RE.match(period_str.replace("　", "").strip())
    if not m:
        return None
    y, m1, d1, m2, d2 = (int(g) for g in m.groups())
    end_year = y if m2 >= m1 else y + 1
    try:
        return datetime(end_year, m2, d2).strftime("%Y-%m-%d")
    except ValueError:
        return None


def _parse_value(cell: str) -> float | None:
    cell = (cell or "").strip().replace(",", "")
    if not cell:
        return None
    try:
        return float(cell)
    except ValueError:
        return None


def _fetch_rows() -> list[tuple[str, float]]:
    """반환: [(ISO 종료일, 억엔 단위 주식 순매수), ...] 시간순."""
    resp = requests.get(CSV_URL, headers=_HEADERS, timeout=20)
    resp.raise_for_status()
    text = resp.content.decode("shift_jis", errors="replace")
    out = []
    for line in text.splitlines():
        cells = next(__import__("csv").reader([line]))
        if not cells or "．" not in cells[0]:
            continue
        date_iso = _parse_period_end_date(cells[0])
        if not date_iso or len(cells) <= _EQUITY_NET_COL:
            continue
        val = _parse_value(cells[_EQUITY_NET_COL])
        if val is None:
            continue
        out.append((date_iso, val))
    out.sort(key=lambda x: x[0])
    return out


def collect_jp_foreign_flow() -> int:
    conn = connect_primary_db(timeout=30)
    try:
        rows = _fetch_rows()
        if not rows:
            conn.execute(
                "INSERT INTO global_macro_collection_log (source, status, records, message) "
                "VALUES ('jp_foreign_flow', 'error', 0, 'no rows parsed from MOF week.csv')"
            )
            conn.commit()
            logger.warning("JP foreign flow: no rows parsed")
            return 0

        total = 0
        prev = None
        for date_iso, oku_yen in rows:
            fx = _fx_rate_near(conn, date_iso)
            if not fx:
                continue
            usd_million = oku_yen * 100.0 / fx  # 억엔×1e8 ÷ fx(엔/달러) ÷ 1e6
            change_pct = ((usd_million - prev) / abs(prev) * 100.0) if prev else None
            conn.execute(
                """
                INSERT INTO global_macro_data (indicator_code, date, value, prev_value, change_pct)
                VALUES ('JP_FOREIGN_FLOW_USD', ?, ?, ?, ?)
                ON CONFLICT(indicator_code, date) DO UPDATE SET
                    value=excluded.value, prev_value=excluded.prev_value, change_pct=excluded.change_pct
                """,
                (date_iso, usd_million, prev, change_pct),
            )
            prev = usd_million
            total += 1

        conn.execute(
            "INSERT INTO global_macro_collection_log (source, status, records, message) "
            "VALUES ('jp_foreign_flow', 'ok', ?, 'MOF week.csv, 주간 대내증권투자 주식 순매수')",
            (total,),
        )
        conn.commit()
        logger.info("JP foreign flow collected %s records", total)
        return total
    except Exception as e:
        logger.warning("JP foreign flow failed: %s", e)
        conn.execute(
            "INSERT INTO global_macro_collection_log (source, status, records, message) VALUES ('jp_foreign_flow', 'error', 0, ?)",
            (str(e),),
        )
        conn.commit()
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(collect_jp_foreign_flow())
