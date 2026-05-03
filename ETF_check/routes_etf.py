import os
import sqlite3
from typing import Dict, Any, List
from fastapi import APIRouter, HTTPException

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
    rows = conn.execute("SELECT DISTINCT trade_date FROM etf_inclusion_daily ORDER BY trade_date DESC LIMIT 6").fetchall()
    return [row["trade_date"] for row in rows]

@router.get("/tab1")
def get_tab1() -> Dict[str, Any]:
    """1번째 탭: 코스피/코스닥 별 ETF 편입 금액이 큰 종목 순"""
    conn = get_db_connection()
    try:
        dates = get_available_dates(conn)
        if not dates:
            return {"kospi": [], "kosdaq": [], "date": None}
        
        latest_date = dates[0]
        
        query = """
            SELECT e.stock_code, m.stock_name, e.etf_amount, m.close as current_price, e.market_cap, e.mktcap_ratio
            FROM etf_inclusion_daily e
            JOIN main_db.stock_universe m ON e.stock_code = m.stock_code
            WHERE e.trade_date = ? AND m.market = ? AND m.stock_type = '보통주'
            ORDER BY e.etf_amount DESC NULLS LAST
            LIMIT 50
        """
        kospi = [format_row(r) for r in conn.execute(query, (latest_date, '유가증권')).fetchall()]
        kosdaq = [format_row(r) for r in conn.execute(query, (latest_date, '코스닥')).fetchall()]
        
        return {"kospi": kospi, "kosdaq": kosdaq, "date": latest_date}
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
        
        def get_increase(prev_date):
            query = """
                SELECT t0.stock_code, m.stock_name, t0.etf_amount as current_amount, 
                       t1.etf_amount as prev_amount, (t0.etf_amount - t1.etf_amount) as amount_diff
                FROM etf_inclusion_daily t0
                JOIN etf_inclusion_daily t1 ON t0.stock_code = t1.stock_code
                LEFT JOIN main_db.stock_universe m ON t0.stock_code = m.stock_code
                WHERE t0.trade_date = ? AND t1.trade_date = ?
                  AND t0.etf_amount IS NOT NULL AND t1.etf_amount IS NOT NULL
                  AND m.stock_type = '보통주'
                ORDER BY amount_diff DESC
                LIMIT 50
            """
            return [format_row(r) for r in conn.execute(query, (latest_date, prev_date)).fetchall()]

        return {
            "1d": get_increase(date_1d),
            "5d": get_increase(date_5d),
            "dates": {"latest": latest_date, "1d": date_1d, "5d": date_5d}
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
        
        # 시총대비 편입금액 증가 = (현재편입금액 - 과거편입금액) / 현재시가총액 * 100
        def get_ratio_increase(prev_date):
            query = """
                SELECT t0.stock_code, m.stock_name, t0.etf_amount as current_amount, t1.etf_amount as prev_amount,
                       (t0.etf_amount - t1.etf_amount) as amount_diff, t0.market_cap,
                       ((t0.etf_amount - t1.etf_amount) / t0.market_cap * 100) as ratio_increase
                FROM etf_inclusion_daily t0
                JOIN etf_inclusion_daily t1 ON t0.stock_code = t1.stock_code
                LEFT JOIN main_db.stock_universe m ON t0.stock_code = m.stock_code
                WHERE t0.trade_date = ? AND t1.trade_date = ?
                  AND t0.etf_amount IS NOT NULL AND t1.etf_amount IS NOT NULL
                  AND t0.market_cap IS NOT NULL AND t0.market_cap > 0
                  AND m.stock_type = '보통주'
                ORDER BY ratio_increase DESC
                LIMIT 50
            """
            return [format_row(r) for r in conn.execute(query, (latest_date, prev_date)).fetchall()]

        return {
            "1d": get_ratio_increase(date_1d),
            "5d": get_ratio_increase(date_5d),
            "dates": {"latest": latest_date, "1d": date_1d, "5d": date_5d}
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
                   (e.etf_amount / e.market_cap * 100) as calc_ratio
            FROM etf_inclusion_daily e
            LEFT JOIN main_db.stock_universe m ON e.stock_code = m.stock_code
            WHERE e.trade_date = ?
              AND e.etf_amount IS NOT NULL AND e.market_cap IS NOT NULL AND e.market_cap > 0
              AND m.stock_type = '보통주'
            ORDER BY calc_ratio DESC
            LIMIT 50
        """
        top = [format_row(r) for r in conn.execute(query, (latest_date,)).fetchall()]
        
        return {"top": top, "date": latest_date}
    finally:
        conn.close()
