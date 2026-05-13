import os
import sqlite3
from typing import Dict, Any, List
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/etf-check", tags=["etf-check"])

DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(DIR, "etf_check.db")
STOCK_DB = "/Applications/stock_dashboard/stock.db"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Attach main DB to filter by market (KOSPI/KOSDAQ)
    conn.execute(f"ATTACH DATABASE '{STOCK_DB}' AS main_db")
    return conn

def format_row(r):
    d = dict(r)
    name = d.get('stock_name')
    if name:
        mapping = {
            "리가켐 바이오사이언스": "리가켐바이오",
            "인텔리안테크놀로지스": "인텔리안테크",
            "YG엔터테인먼트": "와이지엔터테인먼트",
            "에스엠엔터테인먼트": "에스엠"
        }
        if name in mapping:
            d['stock_name'] = mapping[name]
    return d

def get_available_dates(conn) -> List[str]:
    rows = conn.execute("""
        SELECT DISTINCT e.trade_date
        FROM etf_inclusion_daily e
        JOIN collection_log l
          ON l.run_date = e.trade_date
         AND l.status = 'done'
        ORDER BY e.trade_date DESC
        LIMIT 6
    """).fetchall()
    return [row["trade_date"] for row in rows]

# stock_universe.market / secugrp_nm 분포:
#   market='KOSPI'    + secugrp_nm='주권' → 코스피 실제 보통주·우선주 (삼성전자 등)
#   market='유가증권'  + secugrp_nm=NULL  → 코스피 거래소 상장 ETF/ETN 전용
#   market='KOSDAQ'   + secugrp_nm='주권' → 코스닥 실제 보통주
#   market='코스닥'   + secugrp_nm=NULL  → 코스닥 거래소 상장 ETF/ETN 전용
#
# secugrp_nm='주권' 필터만으로 ETF/ETN 전체 배제 가능 (이름 패턴 불필요)
STOCK_FILTER  = "m.secugrp_nm = '주권'"
KOSPI_MARKET  = f"m.market IN ('KOSPI', '유가증권') AND {STOCK_FILTER}"
KOSDAQ_MARKET = f"m.market IN ('KOSDAQ', '코스닥') AND {STOCK_FILTER}"
ALL_MARKET    = STOCK_FILTER

# 하위호환: 기존 코드가 ORDINARY_STOCK_FILTER·ETF_NAME_FILTER를 참조하는 경우 대비
ETF_NAME_FILTER      = "1=1"
ORDINARY_STOCK_FILTER = STOCK_FILTER

@router.get("/tab1")
def get_tab1() -> Dict[str, Any]:
    """1번째 탭: 코스피/코스닥 별 ETF 편입 금액이 큰 종목 순"""
    conn = get_db_connection()
    try:
        dates = get_available_dates(conn)
        if not dates:
            return {"kospi": [], "kosdaq": [], "date": None}

        latest_date = dates[0]

        def fetch_market(market_filter: str):
            query = f"""
                SELECT e.stock_code, m.stock_name, e.etf_amount,
                       m.close as current_price, e.market_cap, e.mktcap_ratio,
                       (SELECT ROUND((ph1.close - ph2.close) * 100.0 / ph2.close, 2)
                        FROM (SELECT close FROM main_db.price_history
                              WHERE stock_code = e.stock_code AND close > 0
                              ORDER BY date DESC LIMIT 1) AS ph1,
                             (SELECT close FROM main_db.price_history
                              WHERE stock_code = e.stock_code AND close > 0
                              ORDER BY date DESC LIMIT 1 OFFSET 1) AS ph2
                        WHERE ph2.close > 0
                       ) AS price_change_pct
                FROM etf_inclusion_daily e
                JOIN main_db.stock_universe m ON e.stock_code = m.stock_code
                WHERE e.trade_date = ?
                  AND {market_filter}
                ORDER BY e.etf_amount DESC NULLS LAST
                LIMIT 50
            """
            return [format_row(r) for r in conn.execute(query, (latest_date,)).fetchall()]

        return {
            "kospi":  fetch_market(KOSPI_MARKET),
            "kosdaq": fetch_market(KOSDAQ_MARKET),
            "date": latest_date,
        }
    finally:
        conn.close()

@router.get("/tab2")
def get_tab2() -> Dict[str, Any]:
    """2번째 탭: 1일/5일 기준 ETF 편입 금액의 증가가 큰 순"""
    conn = get_db_connection()
    try:
        dates = get_available_dates(conn)
        if len(dates) < 2:
            return {"1d": [], "5d": [], "dates": dates}
        
        latest_date = dates[0]
        date_1d = dates[1]
        date_5d = dates[-1] if len(dates) == 6 else dates[-1] # fallback to oldest available if less than 6
        
        def get_change(prev_date, asc: bool = False):
            order = "ASC" if asc else "DESC"
            query = f"""
                SELECT t0.stock_code, m.stock_name, t0.etf_amount as current_amount,
                       t1.etf_amount as prev_amount,
                       (t0.etf_amount - t1.etf_amount) as amount_diff,
                       t0.market_cap
                FROM etf_inclusion_daily t0
                JOIN etf_inclusion_daily t1 ON t0.stock_code = t1.stock_code
                JOIN main_db.stock_universe m ON t0.stock_code = m.stock_code
                WHERE t0.trade_date = ? AND t1.trade_date = ?
                  AND t0.etf_amount IS NOT NULL AND t1.etf_amount IS NOT NULL
                  AND t0.etf_amount > 0 AND t1.etf_amount > 0
                  AND {ALL_MARKET}
                ORDER BY amount_diff {order}
                LIMIT 50
            """
            return [format_row(r) for r in conn.execute(query, (latest_date, prev_date)).fetchall()]

        return {
            "1d":     get_change(date_1d),
            "5d":     get_change(date_5d),
            "1d_dec": get_change(date_1d, asc=True),
            "5d_dec": get_change(date_5d, asc=True),
            "dates": {"latest": latest_date, "1d": date_1d, "5d": date_5d},
        }
    finally:
        conn.close()

@router.get("/tab3")
def get_tab3() -> Dict[str, Any]:
    """3번째 탭: 1일/5일 기준 시가총액 대비 편입금액 증가가 큰 순"""
    conn = get_db_connection()
    try:
        dates = get_available_dates(conn)
        if len(dates) < 2:
            return {"1d": [], "5d": [], "dates": dates}
        
        latest_date = dates[0]
        date_1d = dates[1]
        date_5d = dates[-1] if len(dates) == 6 else dates[-1]
        
        # 시총대비 편입금액 증감 = (현재편입금액 - 과거편입금액) / 현재시가총액 * 100
        def get_ratio_change(prev_date, asc: bool = False):
            order = "ASC" if asc else "DESC"
            query = f"""
                SELECT t0.stock_code, m.stock_name, t0.etf_amount as current_amount,
                       t1.etf_amount as prev_amount,
                       (t0.etf_amount - t1.etf_amount) as amount_diff, t0.market_cap,
                       ((t0.etf_amount - t1.etf_amount) / t0.market_cap * 100) as ratio_increase
                FROM etf_inclusion_daily t0
                JOIN etf_inclusion_daily t1 ON t0.stock_code = t1.stock_code
                JOIN main_db.stock_universe m ON t0.stock_code = m.stock_code
                WHERE t0.trade_date = ? AND t1.trade_date = ?
                  AND t0.etf_amount IS NOT NULL AND t1.etf_amount IS NOT NULL
                  AND t0.etf_amount > 0 AND t1.etf_amount > 0
                  AND t0.market_cap IS NOT NULL AND t0.market_cap > 0
                  AND {ALL_MARKET}
                ORDER BY ratio_increase {order}
                LIMIT 50
            """
            return [format_row(r) for r in conn.execute(query, (latest_date, prev_date)).fetchall()]

        return {
            "1d":     get_ratio_change(date_1d),
            "5d":     get_ratio_change(date_5d),
            "1d_dec": get_ratio_change(date_1d, asc=True),
            "5d_dec": get_ratio_change(date_5d, asc=True),
            "dates": {"latest": latest_date, "1d": date_1d, "5d": date_5d},
        }
    finally:
        conn.close()

@router.get("/tab4")
def get_tab4() -> Dict[str, Any]:
    """4번째 탭: ETF 편입금액이 시가총액 대비 큰 순서"""
    conn = get_db_connection()
    try:
        dates = get_available_dates(conn)
        if not dates:
            return {"top": [], "date": None}
        
        latest_date = dates[0]
        
        query = """
            SELECT e.stock_code, m.stock_name, e.etf_amount, m.close as current_price, e.market_cap,
                   (e.etf_amount / e.market_cap * 100) as calc_ratio,
                   (SELECT ROUND((ph1.close - ph2.close) * 100.0 / ph2.close, 2)
                    FROM (SELECT close FROM main_db.price_history
                          WHERE stock_code = e.stock_code AND close > 0
                          ORDER BY date DESC LIMIT 1) AS ph1,
                         (SELECT close FROM main_db.price_history
                          WHERE stock_code = e.stock_code AND close > 0
                          ORDER BY date DESC LIMIT 1 OFFSET 1) AS ph2
                    WHERE ph2.close > 0
                   ) AS price_change_pct
            FROM etf_inclusion_daily e
            LEFT JOIN main_db.stock_universe m ON e.stock_code = m.stock_code
            WHERE e.trade_date = ?
              AND e.etf_amount IS NOT NULL AND e.market_cap IS NOT NULL AND e.market_cap > 0
              AND """ + ORDINARY_STOCK_FILTER + """
            ORDER BY calc_ratio DESC
            LIMIT 50
        """
        top = [format_row(r) for r in conn.execute(query, (latest_date,)).fetchall()]
        
        return {"top": top, "date": latest_date}
    finally:
        conn.close()

@router.get("/search")
def search_stock_etf(q: str = Query(..., min_length=1, max_length=30)) -> Dict[str, Any]:
    """종목 검색: 최신 ETF 편입액, 시총대비 비중, 5수집일 전 대비 편입액 차이."""
    conn = get_db_connection()
    try:
        dates = get_available_dates(conn)
        if not dates:
            return {"rows": [], "date": None, "compare_date": None, "query": q.strip()}

        latest_date = dates[0]
        compare_date = dates[-1] if len(dates) > 1 else None
        needle = f"%{q.strip()}%"

        query = """
            SELECT e.stock_code,
                   m.stock_name,
                   COALESCE(m.close,
                            (SELECT ph.close FROM main_db.price_history ph
                             WHERE ph.stock_code = e.stock_code AND ph.close > 0
                             ORDER BY ph.date DESC LIMIT 1),
                            e.current_price) AS current_price,
                   CASE
                       WHEN (
                           SELECT ph.close FROM main_db.price_history ph
                           WHERE ph.stock_code = e.stock_code AND ph.close > 0
                           ORDER BY ph.date DESC LIMIT 1 OFFSET 5
                       ) > 0
                       THEN ROUND((
                           (SELECT ph.close FROM main_db.price_history ph
                            WHERE ph.stock_code = e.stock_code AND ph.close > 0
                            ORDER BY ph.date DESC LIMIT 1)
                           -
                           (SELECT ph.close FROM main_db.price_history ph
                            WHERE ph.stock_code = e.stock_code AND ph.close > 0
                            ORDER BY ph.date DESC LIMIT 1 OFFSET 5)
                       ) * 100.0 / (
                           SELECT ph.close FROM main_db.price_history ph
                           WHERE ph.stock_code = e.stock_code AND ph.close > 0
                           ORDER BY ph.date DESC LIMIT 1 OFFSET 5
                       ), 2)
                       ELSE NULL
                   END AS price_change_pct,
                   e.market_cap,
                   e.etf_amount,
                   CASE
                       WHEN e.market_cap IS NOT NULL AND e.market_cap > 0
                       THEN ROUND(e.etf_amount * 100.0 / e.market_cap, 3)
                       ELSE NULL
                   END AS mktcap_ratio,
                   prev.etf_amount AS prev_etf_amount,
                   CASE
                       WHEN prev.etf_amount IS NOT NULL THEN e.etf_amount - prev.etf_amount
                       ELSE NULL
                   END AS amount_diff
            FROM etf_inclusion_daily e
            JOIN main_db.stock_universe m ON e.stock_code = m.stock_code
            LEFT JOIN etf_inclusion_daily prev
                   ON prev.stock_code = e.stock_code AND prev.trade_date = ?
            WHERE e.trade_date = ?
              AND (m.stock_name LIKE ? OR e.stock_code LIKE ?)
              AND """ + ORDINARY_STOCK_FILTER + """
            ORDER BY e.etf_amount DESC NULLS LAST
            LIMIT 50
        """
        rows = [format_row(r) for r in conn.execute(
            query,
            (compare_date or latest_date, latest_date, needle, needle),
        ).fetchall()]
        return {
            "rows": rows,
            "date": latest_date,
            "compare_date": compare_date,
            "query": q.strip(),
        }
    finally:
        conn.close()
