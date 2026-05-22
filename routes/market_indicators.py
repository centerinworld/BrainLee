"""
routes/market_indicators.py — 시장 지표 API

  GET  /api/market-indicators/investor-top      투자자별 순매수 상위종목 (6개 테이블)
  GET  /api/market-indicators/turnover-top      회전율 상위 20종목
  GET  /api/market-indicators/investor-trend    투자자별 매매동향 추이 (지수, 멀티일)
  GET  /api/market-indicators/market-summary    KOSPI/KOSDAQ 현황 요약
  GET  /api/market-indicators/index-investor    오늘의 지수 투자자 순매수 (KOSPI/KOSDAQ)
  GET  /api/market-indicators/short-rank        대차종목순위 (일별, 잔여주식수 상위)
  GET  /api/market-indicators/short-history     종목별 대차거래현황 추이
  GET  /api/market-indicators/short-foreign     내외국인 대차잔고비교 + 거래량 추이
  GET  /api/market-indicators/short-monthly     월별대차거래현황 (집계)
  GET  /api/market-indicators/short-dates       대차종목순위 수집 날짜 목록
"""

from __future__ import annotations

import logging
import sqlite3 as _sl
import time
from datetime import date, datetime, timedelta
from typing import Literal, List, Optional
import re

import requests
from bs4 import BeautifulSoup

from fastapi import APIRouter, Query, Depends
from database import get_db
from db_utils import STOCK_DB_PATH, connect_stock_db

# 단순 메모리 캐시 (key: (date_str, limit), value: (timestamp, data))
_indicator_cache_v5 = {}
CACHE_TTL = 3600  # 1시간
_deposit_cache = {"ts": 0.0, "data": None}
DEPOSIT_CACHE_TTL = 6 * 3600  # 6시간

logger = logging.getLogger(__name__)
router = APIRouter()

DB_PATH = str(STOCK_DB_PATH)

# 한국 증권시장 공휴일 (비영업일) — KRX 기준, 매년 갱신 필요
_KR_HOLIDAYS = {
    # 2025
    "2025-01-01",                                         # 신정
    "2025-01-28", "2025-01-29", "2025-01-30",             # 설날 (1/29)
    "2025-03-01",                                         # 삼일절
    "2025-05-01",                                         # 근로자의날
    "2025-05-05", "2025-05-06",                           # 어린이날+대체
    "2025-05-15",                                         # 부처님오신날
    "2025-06-06",                                         # 현충일
    "2025-08-15",                                         # 광복절
    "2025-10-03", "2025-10-06", "2025-10-07", "2025-10-08", "2025-10-09",  # 추석+개천절+한글날
    "2025-12-25",                                         # 성탄절
    # 2026 — 설날은 2/17 (화), 부처님오신날 5/24 (일)→5/25?, 추석 10/4 (일)
    "2026-01-01",                                         # 신정
    "2026-02-16", "2026-02-17", "2026-02-18",             # 설날 (2/17)
    "2026-03-01",                                         # 삼일절 (일→3/2 대체 확인필요)
    "2026-05-01",                                         # 근로자의날
    "2026-05-05",                                         # 어린이날
    "2026-06-06",                                         # 현충일
    "2026-08-15",                                         # 광복절
    "2026-10-05", "2026-10-06", "2026-10-07",             # 추석 (10/6 화 기준)
    "2026-10-09",                                         # 한글날
    "2026-12-25",                                         # 성탄절
}


def _is_kr_market_open_now(now: datetime | None = None) -> bool:
    """한국 주식 정규장(09:00~15:30, 영업일) 여부."""
    now = now or datetime.now()
    d = now.date()
    ds = d.isoformat()
    if not _is_kr_trading_day(ds):
        return False
    hhmm = now.hour * 100 + now.minute
    return 900 <= hhmm <= 1530


def _is_kr_trading_day(d: str) -> bool:
    """주말 및 공휴일 제외 → 영업일 여부"""
    try:
        dt = date.fromisoformat(d[:10])
    except Exception:
        return False
    if dt.weekday() >= 5:  # 토(5), 일(6)
        return False
    return d[:10] not in _KR_HOLIDAYS


def _db():
    conn = connect_stock_db(timeout=30)
    conn.row_factory = _sl.Row
    return conn


def _to_num(s: str) -> float:
    s = (s or "").strip().replace(",", "")
    if not s:
        return 0.0
    try:
        return float(s)
    except Exception:
        return 0.0


def _parse_yy_mm_dd(s: str) -> str:
    m = re.match(r"^(\d{2})\.(\d{2})\.(\d{2})$", (s or "").strip())
    if not m:
        return ""
    yy, mm, dd = m.groups()
    yyyy = 2000 + int(yy)
    return f"{yyyy:04d}-{int(mm):02d}-{int(dd):02d}"


def _fetch_market_cash_3y() -> dict:
    """네이버 증시자금동향에서 고객예탁금(억원) 3년치 수집."""
    now_ts = time.time()
    if _deposit_cache.get("data") and now_ts - float(_deposit_cache.get("ts") or 0) < DEPOSIT_CACHE_TTL:
        return _deposit_cache["data"]

    cutoff = (date.today() - timedelta(days=365 * 3 + 14)).strftime("%Y-%m-%d")
    headers = {"User-Agent": "Mozilla/5.0"}
    all_rows: list[dict] = []

    # 1페이지당 20영업일. 3년(약 750영업일)+여유치까지 최대 80페이지 순회.
    for page in range(1, 81):
        url = f"https://finance.naver.com/sise/sise_deposit.naver?page={page}"
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            break
        soup = BeautifulSoup(r.text, "html.parser")
        table = soup.select_one("table.type_1") or soup.select_one("table")
        if not table:
            break

        page_rows = []
        for tr in table.select("tr"):
            tds = [td.get_text(strip=True) for td in tr.select("td")]
            if len(tds) < 3:
                continue
            d = _parse_yy_mm_dd(tds[0])
            if not d:
                continue
            dep_krw_100m = _to_num(tds[1])
            cred_krw_100m = _to_num(tds[2])
            page_rows.append(
                {
                    "date": d,
                    "customer_deposit_100m": dep_krw_100m,
                    "credit_balance_100m": cred_krw_100m,
                }
            )

        if not page_rows:
            break

        all_rows.extend(page_rows)
        oldest = page_rows[-1]["date"]
        if oldest <= cutoff:
            break

    # 중복 제거 + 오름차순 정렬 + 컷오프 적용
    uniq = {}
    for r in all_rows:
        uniq[r["date"]] = r
    rows = [uniq[k] for k in sorted(uniq.keys()) if k >= cutoff]

    payload = {
        "source": "naver_finance_sise_deposit",
        "unit": "억원",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "latest_date": rows[-1]["date"] if rows else "",
        "rows": rows,
    }
    _deposit_cache["ts"] = now_ts
    _deposit_cache["data"] = payload
    return payload


def _latest_trade_date(conn: _sl.Connection) -> str:
    """가장 최근 거래일 찾기 (데이터가 충분히 있는 날을 선호)"""
    # 1. 수급 데이터가 50건 이상 있는 가장 최근 날짜 (주말 제외)
    sql = """
        SELECT substr(date, 1, 10) as dt
        FROM price_history
        WHERE (inst_net_buy_amt != 0 OR frn_net_buy_amt != 0)
        GROUP BY dt
        HAVING COUNT(*) >= 1
        ORDER BY dt DESC
        LIMIT 1
    """
    row = conn.execute(sql).fetchone()
    if row:
        return row[0]

    # 2. 데이터가 부족한 경우 단순 최근 날짜
    sql = "SELECT MAX(substr(date, 1, 10)) FROM price_history"
    row = conn.execute(sql).fetchone()
    return row[0] if row and row[0] else datetime.now().strftime("%Y-%m-%d")


# ── GET /api/market-indicators/investor-top ────────────────────
@router.get("/investor-top")
def get_investor_top(
    date_str: str = Query(default="", alias="date"),
    limit: int = Query(default=20, ge=5, le=50),
):
    """
    투자자별(기관/외국인/개인) × 시장별(KOSPI/KOSDAQ) 순매수 상위 종목.
    """
    cache_key = (date_str, limit)
    now = time.time()
    if cache_key in _indicator_cache_v5:
        ts, data = _indicator_cache_v5[cache_key]
        if now - ts < CACHE_TTL:
            if isinstance(data, dict) and "market_open" not in data:
                _indicator_cache_v5.pop(cache_key, None)
            else:
                return data

    conn = _db()
    try:
        trade_date = date_str if date_str else _latest_trade_date(conn)

        # KOSPI 시장명 다양성 대응: '유가증권', 'KOSPI', 'kospi' 포함
        sql = """
            WITH ranked AS (
              SELECT
                ph.stock_code,
                COALESCE(su.stock_name, ph.stock_code) AS stock_name,
                COALESCE(su.market, '') AS market,
                CASE
                    WHEN ph.close > 0 THEN ph.close
                    ELSE (SELECT close FROM price_history WHERE stock_code=ph.stock_code AND close > 0 ORDER BY date DESC LIMIT 1)
                END AS close,
                ph.volume,
                ROUND(ph.inst_net_buy_amt / 100.0, 1) AS inst_amt,   -- 백만원→억원
                ROUND(ph.frn_net_buy_amt  / 100.0, 1) AS frn_amt,
                ROUND(ph.ind_net_buy_amt  / 100.0, 1) AS ind_amt,
                ROUND((COALESCE(ph.inst_net_buy_amt, 0) + COALESCE(ph.frn_net_buy_amt, 0)) / 100.0, 1) AS both_amt,
                ROUND(COALESCE(ph.inst_net_buy, (ph.inst_net_buy_amt * 1000000.0 / NULLIF(ph.close, 0)))) AS inst_qty,
                ROUND(COALESCE(ph.frn_net_buy, (ph.frn_net_buy_amt * 1000000.0 / NULLIF(ph.close, 0))))   AS frn_qty,
                ROUND(COALESCE(ph.ind_net_buy, (ph.ind_net_buy_amt * 1000000.0 / NULLIF(ph.close, 0))))   AS ind_qty,
                ROUND((COALESCE(ph.inst_net_buy, 0) + COALESCE(ph.frn_net_buy, 0))) AS both_qty,
                ph.date
              FROM price_history ph
              LEFT JOIN (
                  SELECT stock_code, stock_name, market, shares_issued, stock_type,
                         ROW_NUMBER() OVER(PARTITION BY stock_code ORDER BY updated_at DESC) as rn
                  FROM stock_universe
              ) su ON ph.stock_code = su.stock_code AND su.rn = 1
              WHERE substr(ph.date, 1, 10) = ?
                AND COALESCE(su.stock_type, '보통주') = '보통주'
                AND COALESCE(su.stock_name, '') NOT LIKE '%ETF%'
                AND COALESCE(su.stock_name, '') NOT LIKE '%ETN%'
                AND (
                    COALESCE(su.stock_name, '') NOT LIKE '%KODEX%' AND
                    COALESCE(su.stock_name, '') NOT LIKE '%TIGER%' AND
                    COALESCE(su.stock_name, '') NOT LIKE '%KBSTAR%' AND
                    COALESCE(su.stock_name, '') NOT LIKE '%ACE%' AND
                    COALESCE(su.stock_name, '') NOT LIKE '%SOL%' AND
                    COALESCE(su.stock_name, '') NOT LIKE '%HANARO%' AND
                    COALESCE(su.stock_name, '') NOT LIKE '%KOSEF%' AND
                    COALESCE(su.stock_name, '') NOT LIKE '%ARIRANG%' AND
                    COALESCE(su.stock_name, '') NOT LIKE '%레버리지%' AND
                    COALESCE(su.stock_name, '') NOT LIKE '%인버스%' AND
                    COALESCE(su.stock_name, '') NOT LIKE '%2X%'
                )
                AND ph.stock_code NOT LIKE '%^%'
                AND ph.stock_code NOT LIKE '%-F'
                AND ph.stock_code NOT LIKE '%=%'
                AND ph.stock_code NOT LIKE 'GC%'
                AND ph.stock_code NOT LIKE 'CL%'
                AND ph.stock_code NOT LIKE 'ES%'
                AND ph.stock_code NOT LIKE 'NQ%'
              GROUP BY ph.stock_code
            )
            SELECT * FROM ranked
        """
        rows = conn.execute(sql, (trade_date,)).fetchall()

        # 당일 주가는 "장중에만" 표시
        market_open = _is_kr_market_open_now()
        today_price_map: dict = {}
        if rows and market_open:
            codes_in = ",".join(f"'{dict(r)['stock_code']}'" for r in rows)
            today_rows = conn.execute(f"""
                SELECT stock_code,
                       close AS today_close,
                       substr(date,1,10) AS today_date
                FROM price_history
                WHERE stock_code IN ({codes_in})
                  AND close > 0
                  AND substr(date,1,10) = ?
                GROUP BY stock_code
                HAVING date = MAX(date)
            """, (datetime.now().strftime("%Y-%m-%d"),)).fetchall()
            for tr in today_rows:
                today_price_map[tr["stock_code"]] = {
                    "today_close": tr["today_close"],
                    "today_date":  tr["today_date"],
                }

        # 시장 분류
        def _mkt(m: str) -> str:
            m = str(m).lower()
            if "유가" in m or "kospi" in m or "코스피" in m:
                return "KOSPI"
            if "코스닥" in m or "kosdaq" in m:
                return "KOSDAQ"
            return "기타"

        kospi_rows  = [r for r in rows if _mkt(r["market"]) == "KOSPI"]
        kosdaq_rows = [r for r in rows if _mkt(r["market"]) == "KOSDAQ"]

        def _enrich(d: dict) -> dict:
            """당일 최신 주가 + 수급기준일 대비 변동률 추가."""
            code = d.get("stock_code", "")
            tp = today_price_map.get(code)
            if tp:
                d["today_close"] = tp["today_close"]
                d["today_date"]  = tp["today_date"]
                base = d.get("close") or 0
                if base > 0:
                    d["today_chg_pct"] = round((tp["today_close"] - base) / base * 100, 2)
                else:
                    d["today_chg_pct"] = None
            else:
                d["today_close"]   = None
                d["today_date"]    = None
                d["today_chg_pct"] = None
            return d

        def _top_buy(lst, col, n=limit):
            """양수(순매수) 상위 N종목 — 내림차순."""
            filtered = [r for r in lst if (r[col] or 0) > 0]
            return [_enrich(dict(r)) for r in sorted(filtered, key=lambda x: x[col], reverse=True)[:n]]

        def _top_sell(lst, col, n=limit):
            """음수(순매도) 상위 N종목 — 절대값 내림차순(가장 많이 판 순)."""
            filtered = [r for r in lst if (r[col] or 0) < 0]
            return [_enrich(dict(r)) for r in sorted(filtered, key=lambda x: x[col])[:n]]

        result = {
            "trade_date": trade_date,
            "market_open": market_open,
            "kospi": {
                "both_buy":  _top_buy(kospi_rows, "both_amt"),
                "inst_buy":  _top_buy(kospi_rows, "inst_amt"),
                "frn_buy":   _top_buy(kospi_rows, "frn_amt"),
                "ind_buy":   _top_buy(kospi_rows, "ind_amt"),
                "both_sell": _top_sell(kospi_rows, "both_amt"),
                "inst_sell": _top_sell(kospi_rows, "inst_amt"),
                "frn_sell":  _top_sell(kospi_rows, "frn_amt"),
                "ind_sell":  _top_sell(kospi_rows, "ind_amt"),
            },
            "kosdaq": {
                "both_buy":  _top_buy(kosdaq_rows, "both_amt"),
                "inst_buy":  _top_buy(kosdaq_rows, "inst_amt"),
                "frn_buy":   _top_buy(kosdaq_rows, "frn_amt"),
                "ind_buy":   _top_buy(kosdaq_rows, "ind_amt"),
                "both_sell": _top_sell(kosdaq_rows, "both_amt"),
                "inst_sell": _top_sell(kosdaq_rows, "inst_amt"),
                "frn_sell":  _top_sell(kosdaq_rows, "frn_amt"),
                "ind_sell":  _top_sell(kosdaq_rows, "ind_amt"),
            },
        }
        
        # 결과 캐싱
        _indicator_cache_v5[cache_key] = (now, result)

        return result
    finally:
        conn.close()


def precompute_indicator_cache():
    """스케줄러에서 호출 — 최신 거래일 investor-top/turnover-top 사전계산."""
    try:
        conn = _db()
        trade_date = _latest_trade_date(conn)
        conn.close()
        # investor-top (캐시 TTL 무시하고 강제 재계산)
        _indicator_cache_v5.pop((trade_date, 20), None)
        _indicator_cache_v5.pop(("", 20), None)
        get_investor_top(date_str=trade_date, limit=20)
        get_turnover_top(date_str=trade_date, market="ALL", limit=20)
        logger.info(f"[시장지표캐시] 사전계산 완료 — 기준일: {trade_date}")
    except Exception as e:
        logger.error(f"[시장지표캐시] 사전계산 실패: {e}")


@router.get("/turnover-top")
def get_turnover_top(
    date_str: str = Query(default="", alias="date"),
    market: str = Query(default="ALL"),
    limit: int = Query(default=20, ge=5, le=50),
):
    """회전율 상위 종목. 회전율 = 거래량 / 상장주식수 × 100"""
    conn = _db()
    try:
        trade_date = date_str if date_str else _latest_trade_date(conn)

        mkt_filter = ""
        params: list = [trade_date]
        if market.upper() == "KOSPI":
            mkt_filter = "AND (su.market LIKE '%유가%' OR su.market LIKE '%KOSPI%' OR su.market LIKE '%코스피%')"
        elif market.upper() == "KOSDAQ":
            mkt_filter = "AND (su.market LIKE '%코스닥%' OR su.market LIKE '%KOSDAQ%')"

        sql = f"""
            SELECT
              ph.stock_code,
              COALESCE(su.stock_name, ph.stock_code) AS stock_name,
              COALESCE(su.market, '') AS market,
              ph.close,
              ph.volume,
              su.shares_issued,
              CASE WHEN su.shares_issued > 0 THEN ROUND(ph.volume * 100.0 / su.shares_issued, 4) ELSE 0 END AS turnover_pct,
              ROUND(ph.inst_net_buy_amt / 100.0) AS inst_net_buy_amt,  -- 백만원→억원
              ROUND(ph.frn_net_buy_amt  / 100.0) AS frn_net_buy_amt,
              COALESCE(su.sector_large, '') AS sector,
              ph.date,
              CASE WHEN ph.trade_amount > 0 THEN ROUND(ph.trade_amount / 1e8, 1)
                   ELSE ROUND(ph.volume * ph.close / 1e8, 1) END AS trade_amount_억,
              COALESCE(su.sector_type, '') AS sector_type
            FROM price_history ph
            LEFT JOIN (
                SELECT stock_code, stock_name, market, shares_issued, sector_large, stock_type, sector_type,
                       ROW_NUMBER() OVER(PARTITION BY stock_code ORDER BY updated_at DESC) as rn
                FROM stock_universe
            ) su ON ph.stock_code = su.stock_code AND su.rn = 1
            WHERE substr(ph.date, 1, 10) = ?
              AND ph.volume > 0
              AND su.shares_issued > 0
              AND COALESCE(su.stock_type, '보통주') = '보통주'
              AND COALESCE(su.stock_name, '') NOT LIKE '%ETF%'
              AND COALESCE(su.stock_name, '') NOT LIKE '%ETN%'
              AND (
                  COALESCE(su.stock_name, '') NOT LIKE '%KODEX%' AND
                  COALESCE(su.stock_name, '') NOT LIKE '%TIGER%' AND
                  COALESCE(su.stock_name, '') NOT LIKE '%KBSTAR%' AND
                  COALESCE(su.stock_name, '') NOT LIKE '%ACE%' AND
                  COALESCE(su.stock_name, '') NOT LIKE '%SOL%' AND
                  COALESCE(su.stock_name, '') NOT LIKE '%HANARO%' AND
                  COALESCE(su.stock_name, '') NOT LIKE '%KOSEF%' AND
                  COALESCE(su.stock_name, '') NOT LIKE '%ARIRANG%' AND
                  COALESCE(su.stock_name, '') NOT LIKE '%레버리지%' AND
                  COALESCE(su.stock_name, '') NOT LIKE '%인버스%' AND
                  COALESCE(su.stock_name, '') NOT LIKE '%2X%'
              )
              {mkt_filter}
            GROUP BY ph.stock_code
            ORDER BY turnover_pct DESC
            LIMIT ?
        """
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()

        # 전일 종가 & 등락률 추가 — correlated subquery로 정확한 직전 거래일 종가
        if rows:
            codes = [r["stock_code"] for r in rows]
            codes_ph = ','.join('?' * len(codes))
            prev_rows = conn.execute(
                f"""SELECT ph.stock_code, ph.close AS prev_close
                    FROM price_history ph
                    WHERE ph.stock_code IN ({codes_ph})
                      AND ph.close > 0
                      AND substr(ph.date, 1, 10) < ?
                      AND ph.date = (
                          SELECT MAX(ph2.date) FROM price_history ph2
                          WHERE ph2.stock_code = ph.stock_code
                            AND ph2.close > 0
                            AND substr(ph2.date, 1, 10) < ?
                      )
                    GROUP BY ph.stock_code""",
                codes + [trade_date, trade_date],
            ).fetchall()
            prev_map = {r["stock_code"]: r["prev_close"] for r in prev_rows}
        else:
            prev_map = {}

        data = []
        for r in rows:
            d = dict(r)
            prev = prev_map.get(d["stock_code"])
            d["prev_close"] = prev
            if prev and prev > 0 and d.get("close"):
                d["chg_pct"] = round((d["close"] - prev) / prev * 100, 2)
            else:
                d["chg_pct"] = None
            data.append(d)

        return {
            "trade_date": trade_date,
            "market":     market,
            "data":       data,
        }
    finally:
        conn.close()


# ── GET /api/market-indicators/investor-trend ──────────────────
@router.get("/investor-trend")
def get_investor_trend(
    market: str  = Query(default="kospi"),
    days:   int  = Query(default=60, ge=5, le=3650),
):
    """
    시장별(KOSPI/KOSDAQ) 기관/외국인/개인 순매수 추이.
    개별 종목 price_history를 stock_universe.market 기준으로 집계.
    지수 종가는 ^KS11 / ^KQ11 에서 별도 조회.
    """
    conn = _db()
    try:
        is_kospi = "kospi" in market.lower()
        market_aliases = ("KOSPI", "유가증권", "코스피") if is_kospi else ("KOSDAQ", "코스닥")
        idx_code = "^KS11"   if is_kospi else "^KQ11"
        since = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")

        # 1. 개별 종목 집계 — 수급 데이터가 실제로 있는 날만 포함
        market_placeholders = ",".join("?" for _ in market_aliases)
        inv_rows = conn.execute(
            f"""WITH su_latest AS (
                 SELECT stock_code, market,
                        ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY updated_at DESC) AS rn
                 FROM stock_universe
               ),
               ph_daily AS (
                 SELECT stock_code,
                        substr(date,1,10) AS d,
                        inst_net_buy_amt, frn_net_buy_amt, ind_net_buy_amt,
                        inst_net_buy, frn_net_buy, ind_net_buy,
                        ROW_NUMBER() OVER (
                          PARTITION BY stock_code, substr(date,1,10)
                          ORDER BY date DESC, rowid DESC
                        ) AS rn
                 FROM price_history
                 WHERE date >= ?
               )
               SELECT ph.d AS d,
                      SUM(COALESCE(ph.inst_net_buy_amt, 0)) / 100.0 AS inst_amt,
                      SUM(COALESCE(ph.frn_net_buy_amt,  0)) / 100.0 AS frn_amt,
                      SUM(COALESCE(ph.ind_net_buy_amt,  0)) / 100.0 AS ind_amt,
                      SUM(COALESCE(ph.inst_net_buy, 0))              AS inst_qty,
                      SUM(COALESCE(ph.frn_net_buy,  0))              AS frn_qty,
                      SUM(COALESCE(ph.ind_net_buy,  0))              AS ind_qty
               FROM ph_daily ph
               JOIN su_latest su ON ph.stock_code = su.stock_code AND su.rn = 1
               WHERE ph.rn = 1
                 AND su.market IN ({market_placeholders})
                 AND strftime('%w', ph.d) NOT IN ('0', '6')
                 AND (ph.inst_net_buy_amt != 0 OR ph.frn_net_buy_amt != 0
                      OR ph.inst_net_buy != 0  OR ph.frn_net_buy != 0)
               GROUP BY ph.d
               ORDER BY ph.d ASC""",
            (since, *market_aliases),
        ).fetchall()

        # 2. 지수 종가 — ^KS11 / ^KQ11
        idx_rows = conn.execute(
            """SELECT substr(date,1,10) AS d, MAX(close) AS close, MAX(volume) AS volume
               FROM price_history
               WHERE stock_code = ? AND date >= ? AND close > 0
                 AND strftime('%w', date) NOT IN ('0', '6')
               GROUP BY d
               ORDER BY d ASC""",
            (idx_code, since),
        ).fetchall()
        idx_map = {r[0]: (r[1], r[2]) for r in idx_rows}

        data = []
        for r in inv_rows:
            d = str(r[0])[:10]
            # 공휴일 필터링 (Python side)
            if not _is_kr_trading_day(d):
                continue
            close, volume = idx_map.get(d, (None, None))
            inst_val = round(r[1])
            frn_val  = round(r[2])
            ind_val  = round(r[3])
            has_supply = (abs(inst_val) > 0 or abs(frn_val) > 0)
            data.append({
                "date":       d,
                "close":      close,
                "volume":     volume,
                "inst_qty":   r[4],
                "frn_qty":    r[5],
                "ind_qty":    r[6],
                "inst_amt":   inst_val if has_supply else None,
                "frn_amt":    frn_val  if has_supply else None,
                "ind_amt":    ind_val  if has_supply else None,
                "has_supply": has_supply,
            })

        # 누적 데이터 추가
        cumulative_inst = 0.0
        cumulative_frn  = 0.0
        for item in data:
            cumulative_inst += (item["inst_amt"] or 0)
            cumulative_frn  += (item["frn_amt"]  or 0)
            item["cum_inst"] = round(cumulative_inst)
            item["cum_frn"]  = round(cumulative_frn)

        return {"market": market.upper(), "days": days, "data": data}
    finally:
        conn.close()


# ── GET /api/market-indicators/market-summary ──────────────────
@router.get("/market-summary")
def get_market_summary():
    """KOSPI / KOSDAQ 시장 요약 (최근 2일 기준)."""
    conn = _db()
    try:
        result = {}
        for mkt_label, code in [("KOSPI", "^KS11"), ("KOSDAQ", "^KQ11")]:
            # close row와 investor row가 날짜별로 분리되어 있으므로 GROUP BY
            rows = conn.execute(
                """SELECT substr(date,1,10) AS d,
                          MAX(close)                                AS close,
                          SUM(COALESCE(inst_net_buy,0))             AS inst_qty,
                          SUM(COALESCE(frn_net_buy,0))              AS frn_qty,
                          SUM(COALESCE(inst_net_buy_amt,0)/100)     AS inst_amt,
                          SUM(COALESCE(frn_net_buy_amt, 0)/100)     AS frn_amt,
                          SUM(COALESCE(ind_net_buy_amt, 0)/100)     AS ind_amt
                   FROM price_history
                   WHERE stock_code=?
                   GROUP BY d
                   HAVING MAX(close) > 0
                   ORDER BY d DESC LIMIT 2""",
                (code,),
            ).fetchall()
            if rows:
                t     = rows[0]
                p     = rows[1] if len(rows) > 1 else None
                chg   = round(t[1] - p[1], 2)         if p and p[1] else 0
                chg_r = round(chg / p[1] * 100, 2)    if p and p[1] else 0
                # inst: prefer _amt column (index 4), fallback to qty (index 2)
                result[mkt_label] = {
                    "close":        t[1],
                    "change":       chg,
                    "change_rate":  chg_r,
                    "date":         str(t[0])[:10],
                    "inst_net_amt": round(t[4] or t[2] or 0),
                    "frn_net_amt":  round(t[5] or t[3] or 0),
                    "ind_net_amt":  round(t[6] or 0),
                }
            else:
                result[mkt_label] = None
        return result
    finally:
        conn.close()


# ── GET /api/market-indicators/index-investor ──────────────────
@router.get("/index-investor")
def get_index_investor(days: int = Query(default=20, ge=1, le=250)):
    """KOSPI + KOSDAQ 지수의 최근 N일 투자자 순매수 일별 데이터."""
    conn = _db()
    try:
        since = (date.today() - timedelta(days=days + 5)).strftime("%Y-%m-%d")
        result = {}
        for mkt_label, code in [("KOSPI", "^KS11"), ("KOSDAQ", "^KQ11")]:
            rows = conn.execute(
                """SELECT substr(date,1,10) AS d,
                          MAX(close) AS close,
                          SUM(COALESCE(inst_net_buy,0))         AS inst_qty,
                          SUM(COALESCE(frn_net_buy,0))          AS frn_qty,
                          SUM(COALESCE(inst_net_buy_amt,0)/100) AS inst_amt,
                          SUM(COALESCE(frn_net_buy_amt, 0)/100) AS frn_amt
                   FROM price_history
                   WHERE stock_code=? AND date >= ?
                     AND strftime('%w', date) NOT IN ('0', '6')
                   GROUP BY d ORDER BY d ASC LIMIT ?""",
                (code, since, days),
            ).fetchall()
            result[mkt_label] = [
                {
                    "date":     str(r[0])[:10],
                    "close":    r[1],
                    "inst_amt": round(r[4] or r[2] or 0),
                    "frn_amt":  round(r[5] or r[3] or 0),
                }
                for r in rows
                if _is_kr_trading_day(str(r[0])[:10])  # 공휴일 제외
            ]
        return result
    finally:
        conn.close()


# ── GET /api/market-indicators/available-dates ─────────────────
@router.get("/available-dates")
def get_available_dates(limit: int = Query(default=30, ge=5, le=250)):
    """회전율/수급 데이터가 있는 최근 영업일 목록.
    주말(토/일)은 제외하며, 1종목 이상 수급 데이터가 있는 날짜 포함."""
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT substr(date,1,10) AS d, count(*) AS cnt
               FROM price_history
               WHERE stock_code NOT LIKE '%^%'
                 AND stock_code NOT LIKE 'GC%'
                 AND stock_code NOT LIKE 'CL%'
                 AND stock_code NOT LIKE 'ES%'
                 AND stock_code NOT LIKE 'NQ%'
                 AND stock_code NOT LIKE '%-F'
                 AND stock_code NOT LIKE '%=%'
                 AND (inst_net_buy_amt != 0 OR frn_net_buy_amt != 0)
                 AND strftime('%w', date) NOT IN ('0', '6')
               GROUP BY d
               HAVING cnt >= 20
               ORDER BY d DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        # 20건 미만인 날도 fallback으로 포함 (주말은 여전히 제외)
        if not rows:
            rows = conn.execute(
                """SELECT substr(date,1,10) AS d, count(*) AS cnt
                   FROM price_history
                   WHERE stock_code NOT LIKE '%^%'
                     AND (COALESCE(inst_net_buy_amt, 0) != 0 OR COALESCE(frn_net_buy_amt, 0) != 0)
                     AND strftime('%w', date) NOT IN ('0', '6')
                   GROUP BY d
                   HAVING cnt >= 1
                   ORDER BY d DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        
        all_dates = [r[0] for r in rows if _is_kr_trading_day(r[0])]
        logger.info(f"Available dates found: {all_dates}")
        return all_dates
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# 대차정보 API (short_rank_daily, short_monthly_stat, short_foreign_*)
# ══════════════════════════════════════════════════════════════════

@router.get("/short-dates")
def get_short_dates(limit: int = 30):
    """대차종목순위 수집된 날짜 목록."""
    conn = _sl.connect(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT DISTINCT bas_dt FROM short_rank_daily ORDER BY bas_dt DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


@router.get("/short-rank")
def get_short_rank(date: str = "", limit: int = 50, sort_by: str = "lnb_rman_stck_cnt"):
    """일별 대차종목순위 (잔여주식수/잔액 상위).

    sort_by: lnb_rman_stck_cnt(잔여주식수) | lnb_bal(잔액) | lnb_ccl_stck_cnt(체결주식수)
    """
    conn = _sl.connect(DB_PATH)
    conn.row_factory = _sl.Row
    try:
        # 날짜 결정
        if not date:
            row = conn.execute("SELECT MAX(bas_dt) FROM short_rank_daily").fetchone()
            date = row[0] if row and row[0] else ""
        if not date:
            return {"date": "", "rows": []}

        valid_cols = {"lnb_rman_stck_cnt", "lnb_bal", "lnb_ccl_stck_cnt"}
        order_col  = sort_by if sort_by in valid_cols else "lnb_rman_stck_cnt"

        rows = conn.execute(f"""
            SELECT s.bas_dt, s.isin_cd, s.stock_code, s.stock_name,
                   s.lnb_ccl_stck_cnt, s.rcal_rdpt_stck_cnt, s.rdpt_stck_cnt,
                   s.lnb_rman_stck_cnt, s.lnb_bal, s.lnb_scrt_dcd,
                   u.market, u.sector_small as sector
            FROM short_rank_daily s
            LEFT JOIN stock_universe u ON s.stock_code = u.stock_code
            WHERE s.bas_dt = ?
              AND s.lnb_scrt_dcd IN ('21','')  -- 주식만 (채권 제외)
            ORDER BY s.{order_col} DESC NULLS LAST
            LIMIT ?
        """, (date, limit)).fetchall()

        return {"date": date, "rows": [dict(r) for r in rows]}
    finally:
        conn.close()


@router.get("/short-history")
def get_short_history(code: str = "", name: str = "", days: int = 60):
    """종목별 대차거래현황 추이 (short_sell_daily 기반)."""
    conn = _sl.connect(DB_PATH)
    conn.row_factory = _sl.Row
    try:
        # 종목코드/이름 해결
        stock_code = code
        stock_name = name
        if not stock_code and name:
            row = conn.execute(
                "SELECT stock_code, stock_name FROM stock_universe WHERE stock_name=? LIMIT 1",
                (name,)
            ).fetchone()
            if row:
                stock_code = row["stock_code"]
                stock_name = row["stock_name"]
        if not stock_code:
            return {"history": [], "stock_name": ""}

        if not stock_name:
            row = conn.execute("SELECT stock_name FROM stock_universe WHERE stock_code=?", (stock_code,)).fetchone()
            stock_name = row["stock_name"] if row else stock_code

        cutoff = (datetime.today() - timedelta(days=days)).strftime("%Y%m%d")
        rows = conn.execute("""
            SELECT bas_dt, borrow_bal_qty, short_qty
            FROM short_sell_daily
            WHERE stock_code = ? AND bas_dt >= ?
              AND stock_code != '000000'
            ORDER BY bas_dt ASC
        """, (stock_code, cutoff)).fetchall()

        history = [{"date": r["bas_dt"], "borrow_bal_qty": r["borrow_bal_qty"], "short_qty": r["short_qty"]} for r in rows]
        return {"stock_code": stock_code, "stock_name": stock_name, "history": history}
    finally:
        conn.close()


@router.get("/short-foreign")
def get_short_foreign(days: int = 120):
    """내외국인 대차잔고비교 + 거래량 추이 (최근 N일)."""
    conn = _sl.connect(DB_PATH)
    conn.row_factory = _sl.Row
    try:
        cutoff = (datetime.today() - timedelta(days=days)).strftime("%Y%m%d")

        bal_rows = conn.execute("""
            SELECT bas_dt, ntiv_brw_bal, forg_brw_bal, brw_bal_forg_rto,
                   ntiv_lndn_bal, forg_lndn_bal, lndn_bal_forg_rto
            FROM short_foreign_balance WHERE bas_dt >= ? ORDER BY bas_dt ASC
        """, (cutoff,)).fetchall()

        trad_rows = conn.execute("""
            SELECT bas_dt, forg_lnb_ccl_stck_cnt, forg_lnb_ccl_amt,
                   ntiv_lnb_ccl_stck_cnt, ntiv_lnb_ccl_amt,
                   sum_lnb_ccl_stck_cnt, sum_lnb_ccl_amt
            FROM short_foreign_trade WHERE bas_dt >= ? ORDER BY bas_dt ASC
        """, (cutoff,)).fetchall()

        return {
            "balance": [dict(r) for r in bal_rows],
            "trade":   [dict(r) for r in trad_rows],
        }
    finally:
        conn.close()


@router.get("/short-monthly")
def get_short_monthly(months: int = 24):
    """월별 대차거래현황 집계 (최근 N개월)."""
    conn = _sl.connect(DB_PATH)
    conn.row_factory = _sl.Row
    try:
        rows = conn.execute("""
            SELECT bas_dt, lnb_expr_itms_cnt, lnb_ccl_stck_cnt, lnb_ccl_amt,
                   lnb_rdpt_stck_cnt, lnb_rdpt_amt, lnb_rman_stck_cnt, lnb_bal
            FROM short_monthly_stat ORDER BY bas_dt DESC LIMIT ?
        """, (months,)).fetchall()
        return {"rows": [dict(r) for r in reversed(rows)]}
    finally:
        conn.close()


@router.get("/market-cash")
def get_market_cash(days: int = Query(default=365 * 3, ge=30, le=365 * 5)):
    """국내 증시 고객예탁금/신용잔고 추이.

    source: 네이버 금융 증시자금동향(sise_deposit)
    unit: 억원
    """
    payload = _fetch_market_cash_3y()
    rows = payload.get("rows", [])
    if not rows:
        return {**payload, "rows": []}

    cutoff = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    trimmed = [r for r in rows if r["date"] >= cutoff]
    return {
        **payload,
        "days": days,
        "rows": trimmed,
    }
