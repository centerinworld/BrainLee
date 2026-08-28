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
import os
from datetime import date, datetime, timedelta
from typing import Literal, List, Optional
import re

import requests
from bs4 import BeautifulSoup

from fastapi import APIRouter, Query, Depends
from database import get_db
from db_utils import STOCK_DB_PATH, connect_stock_db
from trading_calendar import is_kr_trading_day as _tc_is_kr_trading_day

# 단순 메모리 캐시 (key: (date_str, limit), value: (timestamp, data))
_indicator_cache_v5 = {}
CACHE_TTL = 3600  # 1시간
_deposit_cache = {"ts": 0.0, "data": None}
_deposit_cache_naver = {"ts": 0.0, "data": None}
DEPOSIT_CACHE_TTL = 6 * 3600  # 6시간

logger = logging.getLogger(__name__)
router = APIRouter()

DB_PATH = str(STOCK_DB_PATH)

# ── 거래일 판정 — trading_calendar 모듈 위임 (holidays 라이브러리 자동 연도 처리) ──
# 하드코딩된 _KR_HOLIDAYS set 제거: trading_calendar.py에서 일원화 관리


def _is_kr_market_open_now(now: datetime | None = None) -> bool:
    """한국 주식 정규장(09:00~15:30, 영업일) 여부."""
    now = now or datetime.now()
    if not _tc_is_kr_trading_day(now.date()):
        return False
    hhmm = now.hour * 100 + now.minute
    return 900 <= hhmm <= 1530


def _is_kr_trading_day(d: str) -> bool:
    """주말 및 공휴일 제외 → 영업일 여부 (trading_calendar 위임)."""
    try:
        dt = date.fromisoformat(d[:10])
    except Exception:
        return False
    return _tc_is_kr_trading_day(dt)


def _db():
    conn = connect_stock_db(timeout=30)
    conn.row_factory = _sl.Row
    return conn


def _ensure_turnover_signal_tables(conn: _sl.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS turnover_breakout_live_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_ts TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            market TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            score REAL,
            last_price REAL,
            turnover_pct REAL,
            body_pct REAL,
            vol_ratio_20d REAL,
            inst_net_buy_amt_억 REAL,
            frn_net_buy_amt_억 REAL,
            flow_sum_억 REAL,
            tp_price_10pct REAL,
            sl_price_5pct REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tb_live_run_ts ON turnover_breakout_live_log(run_ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tb_live_code_date ON turnover_breakout_live_log(stock_code, trade_date)")
    conn.commit()


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
    """고객예탁금/신용잔고 3년치 수집.
    우선순위:
      1) 한국은행 ECOS (공식 통계)
      2) 네이버 증시자금동향 (fallback)
    """
    now_ts = time.time()
    if _deposit_cache.get("data") and now_ts - float(_deposit_cache.get("ts") or 0) < DEPOSIT_CACHE_TTL:
        return _deposit_cache["data"]

    def _ecos_key() -> str:
        key = (os.getenv("ECOS_API_KEY", "") or os.getenv("BOK_ECOS_API_KEY", "")).strip()
        if key:
            return key
        # 사용자가 전달한 기본 키 파일 fallback
        for p in [
            "/Users/brainlee/Downloads/한국은행ECOS.txt",
            "/Applications/stock_dashboard/한국은행ECOS.txt",
        ]:
            try:
                if os.path.exists(p):
                    v = open(p, "r", encoding="utf-8").read().strip()
                    if v:
                        return v
            except Exception:
                pass
        return ""

    def _ecos_fetch_monthly_series(key: str, stat_code: str, item_code: str, start_ym: str, end_ym: str) -> dict[str, float]:
        url = f"https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/10000/{stat_code}/M/{start_ym}/{end_ym}/{item_code}"
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            return {}
        j = r.json()
        rows = (j.get("StatisticSearch") or {}).get("row") or []
        out: dict[str, float] = {}
        for row in rows:
            ym = str(row.get("TIME") or "")
            if len(ym) != 6:
                continue
            d = f"{ym[:4]}-{ym[4:6]}-01"
            try:
                v = float(str(row.get("DATA_VALUE") or "0").replace(",", ""))
            except Exception:
                v = 0.0
            out[d] = v
        return out

    # ── 1) ECOS 우선 ──────────────────────────────────────────
    try:
        key = _ecos_key()
        if key:
            end = date.today()
            start = end - timedelta(days=365 * 3 + 31)
            start_ym = start.strftime("%Y%m")
            end_ym = end.strftime("%Y%m")

            # 901Y056: 증시주변자금동향
            dep = _ecos_fetch_monthly_series(key, "901Y056", "S23A", start_ym, end_ym)  # 투자자 예탁금
            crd = _ecos_fetch_monthly_series(key, "901Y056", "S23E", start_ym, end_ym)  # 신용융자 잔고
            # 901Y014: 주식시장(월,년) — 시장별 거래대금 (코스피/코스닥 분리)
            kospi_tv = _ecos_fetch_monthly_series(key, "901Y014", "1060000", start_ym, end_ym)
            kosdaq_tv = _ecos_fetch_monthly_series(key, "901Y014", "2060000", start_ym, end_ym)

            keys = sorted(set(dep.keys()) | set(crd.keys()) | set(kospi_tv.keys()) | set(kosdaq_tv.keys()))
            rows = []
            for d in keys:
                dep_won = dep.get(d, 0.0)
                crd_won = crd.get(d, 0.0)
                # 원 → 억원
                rows.append({
                    "date": d,
                    "customer_deposit_100m": round(dep_won / 100_000_000.0, 2),
                    "credit_balance_100m": round(crd_won / 100_000_000.0, 2),
                    "kospi_trade_value_100m": round(kospi_tv.get(d, 0.0) / 100_000_000.0, 2),
                    "kosdaq_trade_value_100m": round(kosdaq_tv.get(d, 0.0) / 100_000_000.0, 2),
                })

            if rows:
                payload = {
                    "source": "ecos_901Y056_901Y014",
                    "unit": "억원",
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "latest_date": rows[-1]["date"],
                    "rows": rows,
                }
                _deposit_cache["ts"] = now_ts
                _deposit_cache["data"] = payload
                return payload
    except Exception as e:
        logger.warning("[market-cash] ECOS fetch failed, fallback to naver: %s", e)

    # ── 2) 네이버 fallback ─────────────────────────────────────
    return _fetch_market_cash_3y_naver()


def _fetch_market_cash_3y_naver() -> dict:
    """네이버 증시자금동향 기준 일별 고객예탁금/신용잔고 3년치."""
    now_ts = time.time()
    if _deposit_cache_naver.get("data") and now_ts - float(_deposit_cache_naver.get("ts") or 0) < DEPOSIT_CACHE_TTL:
        return _deposit_cache_naver["data"]

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
    _deposit_cache_naver["ts"] = now_ts
    _deposit_cache_naver["data"] = payload
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
              ph.open,
              ph.high,
              ph.low,
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


@router.get("/turnover-breakout-signals")
def get_turnover_breakout_signals(
    date_str: str = Query(default="", alias="date"),
    market: str = Query(default="ALL"),
    scan_limit: int = Query(default=200, ge=50, le=1000),
    top_n: int = Query(default=30, ge=5, le=100),
    min_turnover_pct: float = Query(default=8.0, ge=1.0, le=60.0),
    min_body_pct: float = Query(default=5.0, ge=1.0, le=30.0),
    min_vol_ratio: float = Query(default=1.8, ge=1.0, le=10.0),
    require_dual_flow: bool = Query(default=True),
):
    """
    회전율 기반 단기 브레이크아웃 전략 후보.

    기본 아이디어:
    - 회전율 상위에서 유동성 확보
    - 장대양봉 + 거래량 폭발로 수급 집중 구간 포착
    - (선택) 기관/외국인 동반 순매수 필터 적용
    - 기본 리스크룰: 익절 +10%, 손절 -5%
    """
    conn = _db()
    try:
        trade_date = date_str if date_str else _latest_trade_date(conn)

        # 1) 회전율 상위 스캔 풀
        base = get_turnover_top(date_str=trade_date, market=market, limit=scan_limit)
        pool = base.get("data") or []
        if not pool:
            return {
                "trade_date": trade_date,
                "market": market,
                "params": {
                    "scan_limit": scan_limit,
                    "top_n": top_n,
                    "min_turnover_pct": min_turnover_pct,
                    "min_body_pct": min_body_pct,
                    "min_vol_ratio": min_vol_ratio,
                    "require_dual_flow": require_dual_flow,
                },
                "strategy": {
                    "name": "turnover_breakout_v1",
                    "entry_rule": "회전율 + 장대양봉 + 거래량폭발(+수급필터)",
                    "exit_rule": "익절 +10%, 손절 -5%, 또는 3거래일 타임스탑",
                },
                "data_health": {},
                "candidates": [],
            }

        codes = [str(r.get("stock_code") or "").zfill(6) for r in pool if r.get("stock_code")]
        codes = [c for c in codes if c and c != "000000"]
        ph = ",".join("?" for _ in codes)

        # 2) 최근 20거래일 평균 거래량(당일 제외)
        vol_rows = conn.execute(
            f"""
            WITH recent AS (
              SELECT stock_code,
                     volume,
                     ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY date DESC) AS rn
              FROM price_history
              WHERE stock_code IN ({ph})
                AND volume > 0
                AND substr(date,1,10) < ?
            )
            SELECT stock_code, AVG(volume) AS avg20_volume
            FROM recent
            WHERE rn <= 20
            GROUP BY stock_code
            """,
            codes + [trade_date],
        ).fetchall()
        vol_map = {r["stock_code"]: float(r["avg20_volume"] or 0.0) for r in vol_rows}

        # 3) 티커별 최근 틱/분봉 커버리지 체크 (전략 가용성 판단용)
        tick_cov_rows = conn.execute(
            f"""
            SELECT stock_code, COUNT(*) AS cnt
            FROM kiwoom_tick_history
            WHERE stock_code IN ({ph})
              AND substr(event_ts,1,10) = ?
            GROUP BY stock_code
            """,
            codes + [trade_date],
        ).fetchall()
        tick_cov = {r["stock_code"]: int(r["cnt"] or 0) for r in tick_cov_rows}

        min_cov_rows = conn.execute(
            f"""
            SELECT stock_code, COUNT(*) AS cnt
            FROM kiwoom_minute_snapshot
            WHERE stock_code IN ({ph})
              AND substr(minute_ts,1,10) = ?
            GROUP BY stock_code
            """,
            codes + [trade_date],
        ).fetchall()
        min_cov = {r["stock_code"]: int(r["cnt"] or 0) for r in min_cov_rows}

        candidates = []
        for row in pool:
            code = str(row.get("stock_code") or "").zfill(6)
            if not code or code == "000000":
                continue

            o = float(row.get("open") or 0.0)
            h = float(row.get("high") or 0.0)
            l = float(row.get("low") or 0.0)
            c = float(row.get("close") or 0.0)
            v = float(row.get("volume") or 0.0)
            turnover = float(row.get("turnover_pct") or 0.0)
            inst_amt = float(row.get("inst_net_buy_amt") or 0.0)
            frn_amt = float(row.get("frn_net_buy_amt") or 0.0)

            if o <= 0 or h <= 0 or l <= 0 or c <= 0 or v <= 0:
                continue

            avg20 = float(vol_map.get(code) or 0.0)
            vol_ratio = (v / avg20) if avg20 > 0 else 0.0
            body_pct = ((c - o) / o) * 100.0
            wick_up_ratio = ((h - c) / max(h - l, 1e-6)) if h > l else 1.0

            dual_flow_ok = (inst_amt > 0 and frn_amt > 0)
            flow_ok = dual_flow_ok if require_dual_flow else ((inst_amt + frn_amt) > 0)
            candle_ok = (body_pct >= min_body_pct and c >= o and wick_up_ratio <= 0.35)
            vol_ok = (vol_ratio >= min_vol_ratio)
            turnover_ok = (turnover >= min_turnover_pct)

            if not (candle_ok and vol_ok and turnover_ok and flow_ok):
                continue

            # 점수: 회전율/거래량배수/양봉강도/수급합(억원)
            flow_sum = inst_amt + frn_amt
            score = 0.0
            score += min(turnover / 2.0, 30.0)
            score += min(vol_ratio * 10.0, 35.0)
            score += min(body_pct * 3.0, 25.0)
            score += min(max(flow_sum, 0.0) / 5.0, 10.0)

            entry = c
            take_profit = round(entry * 1.10, 2)
            stop_loss = round(entry * 0.95, 2)

            candidates.append({
                "stock_code": code,
                "stock_name": row.get("stock_name"),
                "market": row.get("market"),
                "sector": row.get("sector"),
                "close": c,
                "turnover_pct": round(turnover, 2),
                "body_pct": round(body_pct, 2),
                "vol_ratio_20d": round(vol_ratio, 2),
                "inst_net_buy_amt_억": round(inst_amt, 1),
                "frn_net_buy_amt_억": round(frn_amt, 1),
                "flow_sum_억": round(flow_sum, 1),
                "tick_rows_today": int(tick_cov.get(code, 0)),
                "minute_rows_today": int(min_cov.get(code, 0)),
                "entry_price": round(entry, 2),
                "tp_price_10pct": take_profit,
                "sl_price_5pct": stop_loss,
                "time_stop_days": 3,
                "score": round(score, 2),
            })

        candidates.sort(key=lambda x: (x["score"], x["turnover_pct"]), reverse=True)
        candidates = candidates[:top_n]

        data_health = {
            "trade_date": trade_date,
            "scan_pool_count": len(pool),
            "tick_covered_count": sum(1 for c in codes if tick_cov.get(c, 0) > 0),
            "minute_covered_count": sum(1 for c in codes if min_cov.get(c, 0) > 0),
            "avg20_volume_covered_count": sum(1 for c in codes if (vol_map.get(c, 0) > 0)),
            "note": "틱/분봉은 장중 트리거 정밀도 향상용. 일봉 조건만으로도 후보 산출 가능.",
        }

        return {
            "trade_date": trade_date,
            "market": market,
            "params": {
                "scan_limit": scan_limit,
                "top_n": top_n,
                "min_turnover_pct": min_turnover_pct,
                "min_body_pct": min_body_pct,
                "min_vol_ratio": min_vol_ratio,
                "require_dual_flow": require_dual_flow,
            },
            "strategy": {
                "name": "turnover_breakout_v1",
                "entry_rule": "회전율 상위 + 장대양봉 + 거래량폭발 + 수급필터",
                "exit_rule": "익절 +10%, 손절 -5%, 3거래일 타임스탑",
            },
            "data_health": data_health,
            "candidates": candidates,
        }
    finally:
        conn.close()


@router.get("/turnover-breakout-live")
def get_turnover_breakout_live(
    date_str: str = Query(default="", alias="date"),
    market: str = Query(default="ALL"),
    top_n: int = Query(default=30, ge=5, le=100),
    min_turnover_pct: float = Query(default=5.0, ge=0.5, le=60.0),
    min_body_pct: float = Query(default=2.0, ge=0.5, le=30.0),
    min_vol_ratio: float = Query(default=1.5, ge=1.0, le=10.0),
    require_dual_flow: bool = Query(default=False),
    persist: bool = Query(default=False),
):
    """
    장중 실시간 회전율 브레이크아웃 스캔.
    - 실시간 거래량: kiwoom_realtime_quote.trade_volume
    - 분봉 양봉강도: 최신 minute close vs 해당 분 open
    - 평균거래량: price_history 최근 20거래일
    - 수급: price_history 당일 누적(inst/frn_net_buy_amt)
    """
    conn = _db()
    try:
        _ensure_turnover_signal_tables(conn)
        today = date_str if date_str else datetime.now().strftime("%Y-%m-%d")
        run_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        mkt_filter = ""
        if market.upper() == "KOSPI":
            mkt_filter = "AND (su.market LIKE '%유가%' OR su.market LIKE '%KOSPI%' OR su.market LIKE '%코스피%')"
        elif market.upper() == "KOSDAQ":
            mkt_filter = "AND (su.market LIKE '%코스닥%' OR su.market LIKE '%KOSDAQ%')"

        # 당일 수급(있으면 사용, 없으면 0)
        flow_rows = conn.execute(
            """
            SELECT stock_code,
                   ROUND(SUM(COALESCE(inst_net_buy_amt,0))/100.0,1) AS inst_amt,
                   ROUND(SUM(COALESCE(frn_net_buy_amt,0))/100.0,1)  AS frn_amt
            FROM price_history
            WHERE substr(date,1,10)=?
            GROUP BY stock_code
            """,
            (today,),
        ).fetchall()
        flow_map = {r["stock_code"]: (float(r["inst_amt"] or 0), float(r["frn_amt"] or 0)) for r in flow_rows}

        rows = conn.execute(
            f"""
            WITH su_latest AS (
              SELECT stock_code, stock_name, market, shares_issued, stock_type, sector_large,
                     ROW_NUMBER() OVER(PARTITION BY stock_code ORDER BY updated_at DESC) rn
              FROM stock_universe
            ),
            mq AS (
              SELECT m.stock_code, m.minute_ts, m.open_price, m.close_price, m.sum_volume,
                     ROW_NUMBER() OVER (PARTITION BY m.stock_code ORDER BY m.minute_ts DESC) rn
              FROM kiwoom_minute_snapshot m
              WHERE substr(m.minute_ts,1,10)=?
            )
            SELECT
              q.stock_code,
              su.stock_name,
              su.market,
              su.sector_large AS sector,
              su.shares_issued,
              q.last_price,
              q.trade_volume,
              mq.open_price AS minute_open,
              mq.close_price AS minute_close,
              mq.sum_volume AS minute_volume,
              mq.minute_ts
            FROM kiwoom_realtime_quote q
            JOIN su_latest su ON su.stock_code=q.stock_code AND su.rn=1
            LEFT JOIN mq ON mq.stock_code=q.stock_code AND mq.rn=1
            WHERE su.shares_issued > 0
              AND COALESCE(su.stock_type,'보통주')='보통주'
              AND COALESCE(su.stock_name,'') NOT LIKE '%ETF%'
              AND COALESCE(su.stock_name,'') NOT LIKE '%ETN%'
              {mkt_filter}
            """,
            (today,),
        ).fetchall()

        # tick 기반 fallback 집계 (실시간/분봉 필드가 비어 있을 때 사용)
        tick_rows = conn.execute(
            """
            WITH tday AS (
              SELECT stock_code, event_ts, last_price, trade_volume, substr(event_ts,1,16) AS mkey
              FROM kiwoom_tick_history
              WHERE substr(event_ts,1,10)=?
                AND stock_code IS NOT NULL
            ),
            day_vol AS (
              SELECT stock_code, SUM(COALESCE(trade_volume,0)) AS day_vol, MAX(event_ts) AS max_ts
              FROM tday
              GROUP BY stock_code
            ),
            day_last AS (
              SELECT d.stock_code, d.day_vol, d.max_ts,
                     (SELECT x.last_price FROM tday x WHERE x.stock_code=d.stock_code AND x.event_ts=d.max_ts LIMIT 1) AS day_last_price
              FROM day_vol d
            ),
            last_min AS (
              SELECT stock_code, MAX(mkey) AS mkey
              FROM tday
              GROUP BY stock_code
            ),
            last_min_ticks AS (
              SELECT t.*
              FROM tday t
              JOIN last_min l ON l.stock_code=t.stock_code AND l.mkey=t.mkey
            ),
            minmax AS (
              SELECT stock_code, MIN(event_ts) AS min_ts, MAX(event_ts) AS max_ts, SUM(COALESCE(trade_volume,0)) AS min_vol
              FROM last_min_ticks
              GROUP BY stock_code
            )
            SELECT
              m.stock_code,
              (SELECT x.last_price FROM last_min_ticks x WHERE x.stock_code=m.stock_code AND x.event_ts=m.min_ts LIMIT 1) AS m_open,
              (SELECT x.last_price FROM last_min_ticks x WHERE x.stock_code=m.stock_code AND x.event_ts=m.max_ts LIMIT 1) AS m_close,
              m.min_vol,
              d.day_vol,
              d.day_last_price,
              d.max_ts
            FROM minmax m
            LEFT JOIN day_last d ON d.stock_code=m.stock_code
            """,
            (today,),
        ).fetchall()
        tick_map = {
            str(r["stock_code"]).zfill(6): {
                "m_open": float(r["m_open"] or 0.0),
                "m_close": float(r["m_close"] or 0.0),
                "m_vol": float(r["min_vol"] or 0.0),
                "day_vol": float(r["day_vol"] or 0.0),
                "day_last_price": float(r["day_last_price"] or 0.0),
                "max_ts": r["max_ts"],
            }
            for r in tick_rows if r["stock_code"]
        }

        codes = [str(r["stock_code"]).zfill(6) for r in rows if r["stock_code"]]
        ph = ",".join("?" for _ in codes) if codes else "''"
        avg20_map = {}
        if codes:
            vrows = conn.execute(
                f"""
                WITH recent AS (
                  SELECT stock_code, volume,
                         ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY date DESC) rn
                  FROM price_history
                  WHERE stock_code IN ({ph})
                    AND volume > 0
                    AND substr(date,1,10) < ?
                )
                SELECT stock_code, AVG(volume) avg20
                FROM recent
                WHERE rn<=20
                GROUP BY stock_code
                """,
                codes + [today],
            ).fetchall()
            avg20_map = {r["stock_code"]: float(r["avg20"] or 0.0) for r in vrows}

        cands = []
        for r in rows:
            code = str(r["stock_code"]).zfill(6)
            px = float(r["last_price"] or 0.0)
            vol = float(r["trade_volume"] or 0.0)
            if vol <= 0:
                vol = float(r["minute_volume"] or 0.0)
            t = tick_map.get(code) or {}
            if vol <= 0:
                vol = float(t.get("day_vol") or 0.0)
            shares = float(r["shares_issued"] or 0.0)
            m_open = float(r["minute_open"] or 0.0)
            m_close = float(r["minute_close"] or 0.0)
            if m_open <= 0:
                m_open = float(t.get("m_open") or 0.0)
            if m_close <= 0:
                m_close = float(t.get("m_close") or 0.0)
            if px <= 0:
                px = m_close
            if px <= 0:
                px = float(t.get("day_last_price") or 0.0)
            inst_amt, frn_amt = flow_map.get(code, (0.0, 0.0))
            flow_sum = inst_amt + frn_amt

            if px <= 0 or vol <= 0 or shares <= 0:
                continue
            turnover = (vol * 100.0 / shares)
            body_pct = ((m_close - m_open) / m_open * 100.0) if m_open > 0 else 0.0
            avg20 = float(avg20_map.get(code) or 0.0)
            vol_ratio = (vol / avg20) if avg20 > 0 else 0.0

            flow_ok = (inst_amt > 0 and frn_amt > 0) if require_dual_flow else (flow_sum > 0)
            if not (turnover >= min_turnover_pct and body_pct >= min_body_pct and vol_ratio >= min_vol_ratio and flow_ok):
                continue

            score = min(turnover * 4.0, 35.0) + min(body_pct * 5.0, 30.0) + min(vol_ratio * 10.0, 25.0) + min(max(flow_sum, 0)/5.0, 10.0)
            cands.append({
                "stock_code": code,
                "stock_name": r["stock_name"],
                "market": r["market"],
                "sector": r["sector"],
                "last_price": round(px, 2),
                "turnover_pct": round(turnover, 2),
                "body_pct": round(body_pct, 2),
                "vol_ratio_20d": round(vol_ratio, 2),
                "inst_net_buy_amt_억": round(inst_amt, 1),
                "frn_net_buy_amt_억": round(frn_amt, 1),
                "flow_sum_억": round(flow_sum, 1),
                "minute_ts": r["minute_ts"],
                "entry_price": round(px, 2),
                "tp_price_10pct": round(px * 1.10, 2),
                "sl_price_5pct": round(px * 0.95, 2),
                "time_stop_days": 3,
                "score": round(score, 2),
            })

        cands.sort(key=lambda x: (x["score"], x["turnover_pct"]), reverse=True)
        cands = cands[:top_n]

        if persist and cands:
            for c in cands:
                conn.execute(
                    """
                    INSERT INTO turnover_breakout_live_log
                    (run_ts, trade_date, market, stock_code, stock_name, score, last_price, turnover_pct, body_pct, vol_ratio_20d,
                     inst_net_buy_amt_억, frn_net_buy_amt_억, flow_sum_억, tp_price_10pct, sl_price_5pct)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        run_ts, today, market, c["stock_code"], c["stock_name"], c["score"], c["last_price"], c["turnover_pct"],
                        c["body_pct"], c["vol_ratio_20d"], c["inst_net_buy_amt_억"], c["frn_net_buy_amt_억"], c["flow_sum_억"],
                        c["tp_price_10pct"], c["sl_price_5pct"]
                    ),
                )
            conn.commit()

        return {
            "run_ts": run_ts,
            "trade_date": today,
            "market": market,
            "params": {
                "top_n": top_n,
                "min_turnover_pct": min_turnover_pct,
                "min_body_pct": min_body_pct,
                "min_vol_ratio": min_vol_ratio,
                "require_dual_flow": require_dual_flow,
                "persist": persist,
            },
            "data_health": {
                "realtime_quote_count": len(rows),
                "has_avg20_count": sum(1 for code in codes if (avg20_map.get(code, 0) > 0)),
                "has_flow_today_count": len(flow_map),
            },
            "strategy": {
                "name": "turnover_breakout_live_v1",
                "entry_rule": "실시간 회전율 + 최근 분봉 양봉 + 평균거래량 대비 거래량 배수 + 수급필터",
                "exit_rule": "익절 +10%, 손절 -5%, 3거래일 타임스탑",
            },
            "candidates": cands,
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

        latest_balance_date = bal_rows[-1]["bas_dt"] if bal_rows else ""
        latest_trade_date = trad_rows[-1]["bas_dt"] if trad_rows else ""
        return {
            "balance": [dict(r) for r in bal_rows],
            "trade": [dict(r) for r in trad_rows],
            "latest_balance_date": latest_balance_date,
            "latest_trade_date": latest_trade_date,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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
    # 단기 구간(<=1년)은 일별 해상도 확보를 위해 네이버 우선 사용.
    # 장기 구간(>1년)은 ECOS(공식) 우선 사용.
    payload = _fetch_market_cash_3y_naver() if days <= 365 else _fetch_market_cash_3y()
    rows = payload.get("rows", [])
    if not rows:
        return {**payload, "rows": []}

    cutoff = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    trimmed = [r for r in rows if r["date"] >= cutoff]
    # 월간 계열(ECOS)에서는 단기 구간에 1포인트만 걸리는 경우가 있어
    # 차트가 비어 보이지 않도록 최소 2포인트를 보장한다.
    if len(trimmed) < 2 and rows:
        older = [r for r in rows if r["date"] < cutoff]
        need = 2 - len(trimmed)
        if need > 0 and older:
            trimmed = older[-need:] + trimmed

    return {
        **payload,
        "days": days,
        "rows": trimmed,
    }
