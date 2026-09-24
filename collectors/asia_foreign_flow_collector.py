"""
아시아 외국인 자금 흐름 수집기 (한국 + 대만)

한국(KR_FOREIGN_FLOW_USD): 신규 수집 불필요 — price_history의 ^KS11/^KQ11 지수 레코드
  frn_net_buy(외국인 순매수, 억원 직접 저장)를 날짜별로 합산해 global_macro_data에 적재.
대만(TW_FOREIGN_FLOW_USD): TWSE 공식 공개 JSON(BFI82U, 인증 불필요) 일별 호출.
  https://www.twse.com.tw/rwd/zh/fund/BFI82U?response=json&date=YYYYMMDD

둘 다 USD 백만달러로 환산해 저장 — 국가 간 직접 비교(아시아 합계 유출입) 목적.
환산 환율은 global_macro_data의 KR_USD_KRW / TW_USD_TWD(collectors/yahoo_macro_collector.py)를
날짜에 가장 가까운 값으로 조회한다(정확히 같은 날 없으면 직전 영업일 값 사용).
"""
from __future__ import annotations

from db_compat import connect_primary_db
import logging
import time
from datetime import datetime, timedelta

import requests

logger = logging.getLogger(__name__)

TWSE_URL = "https://www.twse.com.tw/rwd/zh/fund/BFI82U"
_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _fx_rate_near(conn, code: str, date: str) -> float | None:
    row = conn.execute(
        "SELECT value FROM global_macro_data WHERE indicator_code=? AND date<=? "
        "ORDER BY date DESC LIMIT 1",
        (code, date),
    ).fetchone()
    return float(row[0]) if row and row[0] else None


def _upsert(conn, code: str, date: str, value: float, prev: float | None):
    change_pct = ((value - prev) / abs(prev) * 100.0) if prev else None
    conn.execute(
        """
        INSERT INTO global_macro_data (indicator_code, date, value, prev_value, change_pct)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(indicator_code, date) DO UPDATE SET
            value=excluded.value, prev_value=excluded.prev_value, change_pct=excluded.change_pct
        """,
        (code, date, value, prev, change_pct),
    )


def _log(conn, source: str, status: str, records: int, message: str = ""):
    conn.execute(
        "INSERT INTO global_macro_collection_log (source, status, records, message) VALUES (?,?,?,?)",
        (source, status, records, message),
    )


# ── 한국: price_history 집계(신규 네트워크 수집 없음) ────────────────────────

def collect_kr_foreign_flow() -> int:
    """price_history(^KS11+^KQ11) 외국인 순매수(억원) → USD 백만달러 환산, global_macro_data 적재.
    전체 보유 기간을 한 번에 재계산(네트워크 호출 없이 로컬 집계라 저비용)."""
    conn = connect_primary_db(timeout=30)
    try:
        rows = conn.execute(
            """
            SELECT date, SUM(COALESCE(frn_net_buy, 0)) AS won_억
            FROM price_history
            WHERE stock_code IN ('^KS11', '^KQ11')
            GROUP BY date
            HAVING SUM(COALESCE(frn_net_buy, 0)) != 0
            ORDER BY date
            """
        ).fetchall()
        total = 0
        prev = None
        for date, won_억 in rows:
            date = str(date)[:10]
            fx = _fx_rate_near(conn, "KR_USD_KRW", date)
            if not fx:
                continue
            usd_million = float(won_억) * 100.0 / fx  # 억원×100,000,000 ÷ fx ÷ 1,000,000
            _upsert(conn, "KR_FOREIGN_FLOW_USD", date, usd_million, prev)
            prev = usd_million
            total += 1
        _log(conn, "kr_foreign_flow", "ok", total, "price_history ^KS11+^KQ11 frn_net_buy aggregate")
        conn.commit()
        logger.info("KR foreign flow collected %s records", total)
        return total
    except Exception as e:
        logger.warning("KR foreign flow failed: %s", e)
        _log(conn, "kr_foreign_flow", "error", 0, str(e))
        conn.commit()
        return 0
    finally:
        conn.close()


# ── 대만: TWSE BFI82U 공식 공개 JSON ─────────────────────────────────────────

def _fetch_twse_day(date_str: str) -> float | None:
    """date_str: YYYYMMDD. 외자(현지+역외, 자기매매 제외) 순매수(TWD)를 반환. 휴장일이면 None."""
    resp = requests.get(TWSE_URL, params={"response": "json", "date": date_str}, headers=_HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("stat") != "OK" or not data.get("data"):
        return None
    for row in data["data"]:
        label = str(row[0])
        if "外資" in label and "自營商" not in label:
            # ["外資及陸資(不含外資自營商)", 買進金額, 賣出金額, 買賣差額]
            try:
                return float(str(row[3]).replace(",", ""))
            except (ValueError, IndexError):
                return None
    return None


def collect_tw_foreign_flow(days: int = 90) -> int:
    """최근 `days` 캘린더일을 역순으로 순회하며 거래일 데이터만 수집(주말/휴장일은 TWSE가 빈 응답).
    이미 저장된 날짜는 재조회하지 않음(증분 수집)."""
    conn = connect_primary_db(timeout=30)
    try:
        existing = {
            str(r[0]) for r in conn.execute(
                "SELECT date FROM global_macro_data WHERE indicator_code='TW_FOREIGN_FLOW_USD'"
            ).fetchall()
        }
        total = 0
        prev_row = conn.execute(
            "SELECT value FROM global_macro_data WHERE indicator_code='TW_FOREIGN_FLOW_USD' "
            "ORDER BY date DESC LIMIT 1"
        ).fetchone()
        prev = float(prev_row[0]) if prev_row else None

        cur = datetime.now()
        dates_to_try = []
        for i in range(days):
            d = cur - timedelta(days=i)
            iso = d.strftime("%Y-%m-%d")
            if iso not in existing:
                dates_to_try.append(d)
        dates_to_try.sort()  # 과거→최근 순으로 수집해야 prev_value 체인이 맞음

        for d in dates_to_try:
            iso = d.strftime("%Y-%m-%d")
            twd_value = _fetch_twse_day(d.strftime("%Y%m%d"))
            time.sleep(0.3)  # TWSE 예의상 rate limit
            if twd_value is None:
                continue
            fx = _fx_rate_near(conn, "TW_USD_TWD", iso)
            if not fx:
                continue
            usd_million = twd_value / fx / 1_000_000.0
            _upsert(conn, "TW_FOREIGN_FLOW_USD", iso, usd_million, prev)
            prev = usd_million
            total += 1
        _log(conn, "tw_foreign_flow", "ok", total, "TWSE BFI82U official JSON")
        conn.commit()
        logger.info("TW foreign flow collected %s records", total)
        return total
    except Exception as e:
        logger.warning("TW foreign flow failed: %s", e)
        _log(conn, "tw_foreign_flow", "error", 0, str(e))
        conn.commit()
        return 0
    finally:
        conn.close()


def collect_asia_foreign_flow(tw_days: int = 90) -> int:
    """반환값은 총 레코드 수(int) — scripts/ops/collect_global_macro_daily.py의
    run_step()이 int(records)를 기대하므로 dict가 아닌 합계를 반환한다."""
    kr = collect_kr_foreign_flow()
    tw = collect_tw_foreign_flow(days=tw_days)
    logger.info("Asia foreign flow: KR=%d TW=%d", kr, tw)
    return kr + tw


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(collect_asia_foreign_flow())
