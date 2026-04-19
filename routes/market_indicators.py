"""
routes/market_indicators.py — 시장 지표 API

  GET  /api/market-indicators/investor-top      투자자별 순매수 상위종목 (6개 테이블)
  GET  /api/market-indicators/turnover-top      회전율 상위 20종목
  GET  /api/market-indicators/investor-trend    투자자별 매매동향 추이 (지수, 멀티일)
  GET  /api/market-indicators/market-summary    KOSPI/KOSDAQ 현황 요약
  GET  /api/market-indicators/index-investor    오늘의 지수 투자자 순매수 (KOSPI/KOSDAQ)
  GET  /api/market-indicators/futures           KOSPI200/KOSDAQ150 선물 현황 (KRX API)
"""

from __future__ import annotations

import logging
import sqlite3 as _sl
import time
from datetime import date, datetime, timedelta
from typing import Literal, List, Optional

from fastapi import APIRouter, Query, Depends
from database import get_db

# 단순 메모리 캐시 (key: (date_str, limit), value: (timestamp, data))
_indicator_cache_v5 = {}
CACHE_TTL = 3600  # 1시간

logger = logging.getLogger(__name__)
router = APIRouter()

DB_PATH = "stock.db"


def _db():
    conn = _sl.connect(DB_PATH)
    conn.row_factory = _sl.Row
    return conn


def _latest_trade_date(conn: _sl.Connection) -> str:
    """가장 최근 거래일 찾기 (수량 또는 금액 수급 데이터가 있는 날)"""
    # amt 또는 qty 기준으로 수급 데이터가 있는 가장 최근 날짜
    sql = """
        SELECT substr(date, 1, 10) as dt
        FROM price_history
        WHERE (inst_net_buy_amt != 0 OR frn_net_buy_amt != 0
               OR inst_net_buy != 0 OR frn_net_buy != 0)
          AND stock_code NOT LIKE '%^%'
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
                -- amt 우선, 없으면 qty×종가로 역산 (백만원→억원: /100, qty×원→억원: /1e8)
                CASE WHEN COALESCE(ph.inst_net_buy_amt,0) != 0
                     THEN ROUND(ph.inst_net_buy_amt / 100.0, 1)
                     ELSE ROUND(COALESCE(ph.inst_net_buy,0) * COALESCE(NULLIF(ph.close,0),0) / 1e8, 1)
                END AS inst_amt,
                CASE WHEN COALESCE(ph.frn_net_buy_amt,0) != 0
                     THEN ROUND(ph.frn_net_buy_amt / 100.0, 1)
                     ELSE ROUND(COALESCE(ph.frn_net_buy,0) * COALESCE(NULLIF(ph.close,0),0) / 1e8, 1)
                END AS frn_amt,
                CASE WHEN COALESCE(ph.ind_net_buy_amt,0) != 0
                     THEN ROUND(ph.ind_net_buy_amt / 100.0, 1)
                     ELSE ROUND(COALESCE(ph.ind_net_buy,0) * COALESCE(NULLIF(ph.close,0),0) / 1e8, 1)
                END AS ind_amt,
                CASE WHEN COALESCE(ph.inst_net_buy_amt,0) != 0 OR COALESCE(ph.frn_net_buy_amt,0) != 0
                     THEN ROUND((COALESCE(ph.inst_net_buy_amt,0) + COALESCE(ph.frn_net_buy_amt,0)) / 100.0, 1)
                     ELSE ROUND((COALESCE(ph.inst_net_buy,0) + COALESCE(ph.frn_net_buy,0)) * COALESCE(NULLIF(ph.close,0),0) / 1e8, 1)
                END AS both_amt,
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
                AND (su.stock_type = '보통주' OR su.stock_type IS NULL)
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

        # 당일 최신 주가 조회 (trade_date 다음날 이후 최신 close)
        today_price_map: dict = {}
        if rows:
            codes_in = ",".join(f"'{dict(r)['stock_code']}'" for r in rows)
            today_rows = conn.execute(f"""
                SELECT stock_code,
                       close AS today_close,
                       substr(date,1,10) AS today_date
                FROM price_history
                WHERE stock_code IN ({codes_in})
                  AND close > 0
                  AND substr(date,1,10) > ?
                GROUP BY stock_code
                HAVING date = MAX(date)
            """, (trade_date,)).fetchall()
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
        # 캐시 전체 초기화 후 재계산 (조건 변경으로 인한 stale 캐시 방지)
        _indicator_cache_v5.clear()
        get_investor_top(date_str=trade_date, limit=20)
        get_turnover_top(date_str=trade_date, market="ALL", limit=20)
        logger.info(f"[시장지표캐시] 사전계산 완료 — 기준일: {trade_date}")
    except Exception as e:
        logger.error(f"[시장지표캐시] 사전계산 실패: {e}")


@router.post("/cache/clear")
def clear_indicator_cache():
    """캐시 수동 초기화 (서버 재시작 없이 갱신)."""
    count = len(_indicator_cache_v5)
    _indicator_cache_v5.clear()
    return {"cleared": count}


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
              ph.date
            FROM price_history ph
            LEFT JOIN (
                SELECT stock_code, stock_name, market, shares_issued, sector_large, stock_type,
                       ROW_NUMBER() OVER(PARTITION BY stock_code ORDER BY updated_at DESC) as rn
                FROM stock_universe
            ) su ON ph.stock_code = su.stock_code AND su.rn = 1
            WHERE substr(ph.date, 1, 10) = ?
              AND ph.volume > 0
              AND su.shares_issued > 0
              AND (su.stock_type = '보통주' OR su.stock_type IS NULL)
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

        # 전일 종가 & 등락률 추가
        if rows:
            codes = [r["stock_code"] for r in rows]
            prev_rows = conn.execute(
                f"""SELECT stock_code, close AS prev_close
                    FROM price_history
                    WHERE stock_code IN ({','.join('?'*len(codes))})
                      AND close > 0
                      AND substr(date, 1, 10) < ?
                    GROUP BY stock_code
                    HAVING date = MAX(date)""",
                codes + [trade_date],
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
    지수(KOSPI/KOSDAQ) 기관/외국인/개인 순매수 추이.
    price_history 의 ^KS11 / ^KQ11 에 inst_net_buy / frn_net_buy 기록됨.
    """
    conn = _db()
    try:
        code = "^KS11" if "kospi" in market.lower() else "^KQ11"
        since = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
        # 지수는 날짜별 2개 row 존재 (close row + investor row) → 집계
        rows = conn.execute(
            """SELECT substr(date,1,10) AS d,
                      MAX(close)                             AS close,
                      MAX(volume)                            AS volume,
                      SUM(COALESCE(inst_net_buy, 0))         AS inst_qty,
                      SUM(COALESCE(frn_net_buy, 0))          AS frn_qty,
                      SUM(COALESCE(ind_net_buy, 0))          AS ind_qty,
                      SUM(COALESCE(inst_net_buy_amt, 0)/100) AS inst_amt,
                      SUM(COALESCE(frn_net_buy_amt,  0)/100) AS frn_amt,
                      SUM(COALESCE(ind_net_buy_amt,  0)/100) AS ind_amt
               FROM price_history
               WHERE stock_code = ? AND date >= ?
               GROUP BY d
               HAVING MAX(close) > 0
               ORDER BY d ASC""",
            (code, since),
        ).fetchall()

        data = []
        for r in rows:
            # inst/frn/ind: 지수 데이터는 inst_net_buy 컬럼에 억원 단위로 저장됨
            inst_val = round(r[6] or r[3] or 0)  # amt 있으면 우선, 없으면 qty(=억원)
            frn_val  = round(r[7] or r[4] or 0)
            ind_val  = round(r[8] or r[5] or 0)
            data.append({
                "date":     str(r[0])[:10],
                "close":    r[1],
                "volume":   r[2],
                "inst_qty": r[3],
                "frn_qty":  r[4],
                "ind_qty":  r[5],
                "inst_amt": inst_val,
                "frn_amt":  frn_val,
                "ind_amt":  ind_val,
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
            ]
        return result
    finally:
        conn.close()


# ── GET /api/market-indicators/available-dates ─────────────────
@router.get("/available-dates")
def get_available_dates(limit: int = Query(default=30, ge=5, le=250)):
    """최근 영업일 목록 (주말 제외, 가격 데이터 20종목 이상인 날).
    수급 데이터 없는 날도 포함하여 당일처럼 수급 미수집일이 누락되는 문제 해결."""
    conn = _db()
    try:
        # 가격 데이터 기준 (수급 여부 불문) — 20종목 이상이면 영업일로 간주
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
                 AND close > 0
                 AND strftime('%w', date) NOT IN ('0', '6')
               GROUP BY d
               HAVING cnt >= 20
               ORDER BY d DESC LIMIT ?""",
            (limit,),
        ).fetchall()

        from datetime import datetime, timezone, timedelta
        KST = timezone(timedelta(hours=9))
        now_kst = datetime.now(KST)
        # 16시 이전이면 오늘 날짜 제외 (장 마감 전 수급 데이터 미확정)
        if now_kst.hour < 16:
            today_str = now_kst.strftime("%Y-%m-%d")
            all_dates = [r[0] for r in rows if r[0] < today_str]
        else:
            all_dates = [r[0] for r in rows]
        logger.info(f"Available dates: {len(all_dates)}, latest: {all_dates[:3] if all_dates else []}")
        return all_dates
    finally:
        conn.close()


# ── GET /api/market-indicators/futures ─────────────────────────
_futures_cache: dict = {}   # {date_str: (ts, data)}
_FUTURES_CACHE_TTL = 60     # 1분 (실시간에 가깝게)

_KRX_BASE = "https://data-dbg.krx.co.kr/svc/apis"

def _krx_headers() -> dict:
    try:
        import config as _cfg
        return {"AUTH_KEY": _cfg.KRX_API_KEY}
    except Exception:
        return {}


def _n(val) -> float:
    """KRX 응답값 → float 변환 (쉼표 제거)"""
    try:
        return float(str(val).replace(",", "")) if val not in ("", "-", None) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _fetch_krx_futures(bas_dd: str) -> list[dict]:
    """KRX API drv/fut_bydd_trd 호출 → 선물 일별 현황"""
    import requests as _req
    url = f"{_KRX_BASE}/drv/fut_bydd_trd"
    try:
        r = _req.get(url, params={"basDd": bas_dd}, headers=_krx_headers(), timeout=10)
        if r.status_code == 200:
            return r.json().get("OutBlock_1", [])
    except Exception as e:
        logger.warning(f"[futures] KRX fut_bydd_trd {bas_dd}: {e}")
    return []


def _fetch_krx_night_futures(bas_dd: str) -> list[dict]:
    """KRX API drv/ngt_fut_bydd_trd 호출 → 야간선물 일별 현황"""
    import requests as _req
    url = f"{_KRX_BASE}/drv/ngt_fut_bydd_trd"
    try:
        r = _req.get(url, params={"basDd": bas_dd}, headers=_krx_headers(), timeout=10)
        if r.status_code == 200:
            return r.json().get("OutBlock_1", [])
    except Exception as e:
        logger.warning(f"[futures] KRX ngt_fut_bydd_trd {bas_dd}: {e}")
    return []


def _pick_front_month(rows: list[dict], name_keyword: str) -> dict | None:
    """종목명에 키워드 포함된 계약 중 최근월물(만기 가장 빠른 것) 선택"""
    matched = [r for r in rows if name_keyword in str(r.get("ISU_ABBRV", "") or r.get("ISU_NM", ""))]
    if not matched:
        return None
    # 종목코드 기준 정렬: 근월물이 보통 코드 순서상 앞에 옴
    matched.sort(key=lambda r: str(r.get("ISU_CD", "")))
    return matched[0]


def _row_to_futures_item(r: dict, label: str, color: str, session: str = "정규") -> dict:
    close   = _n(r.get("TDD_CLSPRC") or r.get("CLSPRC"))
    prev    = _n(r.get("BASISPRC") or r.get("BAS_PRC"))
    chg     = _n(r.get("CMPPREVDD_PRC") or r.get("CMPPRVDD_PRC"))
    chg_pct = _n(r.get("FLUC_RT"))
    volume  = _n(r.get("ACC_TRDVOL") or r.get("TDD_VOL"))
    oi      = _n(r.get("OPNINT_QTY") or r.get("OPNINTQTY"))
    isu_cd  = str(r.get("ISU_CD", ""))
    name    = str(r.get("ISU_ABBRV") or r.get("ISU_NM") or label)

    # chg_pct가 없거나 0이면 직접 계산
    if chg_pct == 0 and prev > 0 and close > 0:
        chg_pct = round((close - prev) / prev * 100, 2)
    if chg == 0 and close > 0 and prev > 0:
        chg = round(close - prev, 2)

    return {
        "label":    label,
        "name":     name,
        "code":     isu_cd,
        "session":  session,
        "color":    color,
        "close":    close,
        "change":   chg,
        "change_pct": chg_pct,
        "volume":   int(volume),
        "open_interest": int(oi),
    }


@router.get("/futures")
def get_futures():
    """
    KOSPI200 선물 / KOSDAQ150 선물 / KOSPI200 야간선물 현황.
    KRX API drv/fut_bydd_trd (정규) + drv/ngt_fut_bydd_trd (야간) 사용.
    데이터는 60초 캐싱.
    """
    today = date.today()
    # 주말이면 가장 최근 금요일 기준
    wd = today.weekday()
    if wd == 5:   # 토
        today = today - timedelta(days=1)
    elif wd == 6: # 일
        today = today - timedelta(days=2)
    bas_dd = today.strftime("%Y%m%d")

    now = time.time()
    cached = _futures_cache.get(bas_dd)
    if cached and (now - cached[0]) < _FUTURES_CACHE_TTL:
        return cached[1]

    items: list[dict] = []

    # ── 정규선물 ──
    reg_rows = _fetch_krx_futures(bas_dd)
    if reg_rows:
        k200 = _pick_front_month(reg_rows, "KOSPI200")
        kq150 = _pick_front_month(reg_rows, "KOSDAQ150")
        if k200:
            items.append(_row_to_futures_item(k200, "KOSPI200 선물", "#34d399", "정규"))
        if kq150:
            items.append(_row_to_futures_item(kq150, "KOSDAQ150 선물", "#60a5fa", "정규"))

    # ── 야간선물 ──
    ngt_rows = _fetch_krx_night_futures(bas_dd)
    if ngt_rows:
        nk200 = _pick_front_month(ngt_rows, "KOSPI200")
        if nk200:
            items.append(_row_to_futures_item(nk200, "KOSPI200 야간선물", "#a78bfa", "야간"))

    result = {
        "items":   items,
        "bas_dd":  bas_dd,
        "note":    "KRX 정규선물(drv/fut_bydd_trd) + 야간선물(drv/ngt_fut_bydd_trd) 근월물 기준",
        "updated": datetime.now().strftime("%H:%M:%S"),
    }
    _futures_cache[bas_dd] = (now, result)
    return result
