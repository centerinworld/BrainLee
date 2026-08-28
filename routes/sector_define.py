import sqlite3
import os
import pandas as pd
from typing import List, Optional, Dict
from pydantic import BaseModel
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter()

logger = logging.getLogger(__name__)
BASE_DIR = "/Applications/stock_dashboard"
DB_PATH = os.path.join(BASE_DIR, "stock.db")
EMP_DB_PATH = os.path.join(BASE_DIR, "employment_monitor/employment.db")
TRADE_DB_PATH = os.path.join(BASE_DIR, "hs_trade_lab/data/hs_trade_lab.db")


def _json_safe(value):
    """Convert pandas/numpy scalar values before FastAPI JSON encoding."""
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value

class StockInfo(BaseModel):
    category: str
    stock_name: str
    stock_code: Optional[str]
    price: Optional[float]
    chg_pct: Optional[float]
    market_cap: Optional[float]
    pbr: Optional[float]
    per: Optional[float]
    ref_price: Optional[float]
    ref_chg_pct: Optional[float]

class PostDetail(BaseModel):
    id: int
    title: str
    blog_url: str
    post_date: str
    ai_summary: Optional[str]
    stocks: List[StockInfo]

def get_db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@router.get("/posts")
def get_posts():
    conn = get_db_conn()
    try:
        # 테이블 존재 여부 확인 및 자동 생성
        conn.execute("CREATE TABLE IF NOT EXISTS sector_posts (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, blog_url TEXT NOT NULL, post_date TEXT NOT NULL, ai_summary TEXT, telegram_sent INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        rows = conn.execute("SELECT * FROM sector_posts ORDER BY post_date DESC, id DESC").fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error fetching posts: {e}")
        return []
    finally:
        conn.close()

@router.get("/post/{post_id}")
def get_post_detail(post_id: int):
    conn = get_db_conn()
    try:
        # 테이블 존재 여부 확인 및 자동 생성
        conn.execute("CREATE TABLE IF NOT EXISTS sector_stocks (id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER, category TEXT, stock_name TEXT, stock_code TEXT, ref_price REAL, memo TEXT, FOREIGN KEY(post_id) REFERENCES sector_posts(id))")
        
        post = conn.execute("SELECT * FROM sector_posts WHERE id=?", (post_id,)).fetchone()
        if not post:
            raise HTTPException(status_code=404, detail="Post not found")
            
        stocks_rows = conn.execute("SELECT * FROM sector_stocks WHERE post_id=?", (post_id,)).fetchall()
        
        result_stocks = []
        for s in stocks_rows:
            code = s["stock_code"]
            s_name = s["stock_name"]
            
            # stock.db에서 실시간/최신 정보 보완
            price = None
            chg_pct = None
            market_cap = None
            pbr = None
            per = None
            
            if code:
                # 최신 가격 및 변동률
                p_row = conn.execute(
                    "SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 2",
                    (code,)
                ).fetchall()
                if p_row:
                    price = p_row[0]["close"]
                    if len(p_row) > 1:
                        prev_close = p_row[1]["close"]
                        chg_pct = (price - prev_close) / prev_close * 100 if prev_close else 0
                
                # 시총, PBR, PER
                u_row = conn.execute(
                    "SELECT market_cap, per, pbr FROM stock_universe WHERE stock_code=?",
                    (code,)
                ).fetchone()
                if u_row:
                    market_cap = u_row["market_cap"]
                    per = u_row["per"]
                    pbr = u_row["pbr"]
            
            # 기준가 대비 변동률
            ref_price = s["ref_price"]
            ref_chg_pct = None
            if price and ref_price and ref_price > 0:
                ref_chg_pct = (price - ref_price) / ref_price * 100
                
            result_stocks.append({
                "category": s["category"],
                "stock_name": s_name,
                "stock_code": code,
                "price": price,
                "chg_pct": chg_pct,
                "market_cap": market_cap,
                "pbr": pbr,
                "per": per,
                "ref_price": ref_price,
                "ref_chg_pct": ref_chg_pct
            })
            
        return {
            **dict(post),
            "stocks": result_stocks
        }
    finally:
        conn.close()

@router.post("/parse")
async def trigger_parse(background_tasks: BackgroundTasks):
    from Sector_define.blog_parser import run_parser
    background_tasks.add_task(run_parser)
    return {"message": "Blog parsing started in background"}

@router.post("/init")
def init_sector_tables():
    conn = get_db_conn()
    try:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS sector_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            blog_url TEXT NOT NULL,
            post_date TEXT NOT NULL,
            ai_summary TEXT,
            telegram_sent INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS sector_stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER,
            category TEXT,
            stock_name TEXT,
            stock_code TEXT,
            ref_price REAL,
            memo TEXT,
            FOREIGN KEY(post_id) REFERENCES sector_posts(id)
        )
        """)
        conn.commit()
        return {"message": "Sector tables initialized in stock.db"}
    finally:
        conn.close()


@router.post("/post")
async def create_post(payload: dict):
    """새 섹터 분석 포스트 추가"""
    conn = get_db_conn()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS sector_posts (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, blog_url TEXT NOT NULL, post_date TEXT NOT NULL, ai_summary TEXT, telegram_sent INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        cur = conn.execute(
            "INSERT INTO sector_posts (title, blog_url, post_date, ai_summary) VALUES (?,?,?,?)",
            (payload.get("title",""), payload.get("blog_url",""), payload.get("post_date",""), payload.get("ai_summary",""))
        )
        post_id = cur.lastrowid
        stocks = payload.get("stocks", [])
        for s in stocks:
            stock_name = s.get("stock_name", "")
            code = s.get("stock_code")
            if not stock_name and code:
                row = conn.execute("SELECT stock_name FROM stock_universe WHERE stock_code=?", (code,)).fetchone()
                if row:
                    stock_name = row["stock_name"]
            conn.execute(
                "INSERT INTO sector_stocks (post_id, category, stock_name, stock_code, ref_price, memo) VALUES (?,?,?,?,?,?)",
                (post_id, s.get("category",""), stock_name, code, s.get("ref_price"), s.get("memo",""))
            )
        conn.commit()
        # 새 종목이 있으면 stock_universe에 없는 종목은 추가 트리거
        new_codes = [s.get("stock_code") for s in stocks if s.get("stock_code") and s["stock_code"].isdigit() and len(s["stock_code"])==6]
        return {"id": post_id, "new_codes": new_codes, "message": f"포스트 등록 완료 (종목 {len(stocks)}개)"}
    finally:
        conn.close()

@router.delete("/post/{post_id}")
def delete_post(post_id: int):
    conn = get_db_conn()
    try:
        conn.execute("DELETE FROM sector_stocks WHERE post_id=?", (post_id,))
        conn.execute("DELETE FROM sector_posts WHERE id=?", (post_id,))
        conn.commit()
        return {"message": f"포스트 {post_id} 삭제 완료"}
    finally:
        conn.close()

@router.get("/special-filter")
def get_special_filtered_stocks(
    opm_threshold: float = 0.03,
    depr_threshold: float = 0.40,
    emp_months: int = 3,
    min_score: int = 2
):
    """
    Hot Sector 내 종목들을 대상으로 특수 필터링 수행
    - 조건1: 감가상각비/매출액 >= depr_threshold
    - 조건2: 영업이익률 <= opm_threshold 또는 적자
    - 조건3: 최근 emp_months 개월간 인원 증가
    - 조건4: 최근 1개월 수출액 증가
    """
    conn_stock = sqlite3.connect(DB_PATH)
    conn_stock.row_factory = sqlite3.Row
    
    try:
        # 1. 분석 대상 종목 목록 추출 (Hot Sector + Tenbagger 후보군)
        stocks_rows = conn_stock.execute("""
            SELECT DISTINCT stock_code, stock_name 
            FROM (
                SELECT stock_code, stock_name FROM sector_stocks
                UNION
                SELECT stock_code, stock_name FROM tenbagger_results
            )
            WHERE stock_code IS NOT NULL
        """).fetchall()
        stocks = [dict(r) for r in stocks_rows]
        codes = [s['stock_code'] for s in stocks]
        if not codes:
            return {"stocks": []}

        # 2. 데이터 로드 (최적화 위해 필요한 부분만 추출)
        # 재무 데이터
        financials = pd.read_sql_query(f"""
            SELECT stock_code, year, quarter, revenue, operating_profit, depreciation_amortization
            FROM financial_data
            WHERE stock_code IN ({','.join(['?']*len(codes))}) AND is_annual = 0 AND quarter > 0
            ORDER BY year DESC, quarter DESC
        """, conn_stock, params=codes)

        cash_flows = pd.read_sql_query(f"""
            SELECT stock_code, year, quarter, depreciation
            FROM cash_flow_data
            WHERE stock_code IN ({','.join(['?']*len(codes))}) AND is_annual = 0 AND quarter > 0
            ORDER BY year DESC, quarter DESC
        """, conn_stock, params=codes)

        # 고용 데이터
        conn_emp = sqlite3.connect(EMP_DB_PATH)
        emp_data = pd.read_sql_query(f"""
            SELECT stock_code, ym, worker_count
            FROM employment_company
            WHERE stock_code IN ({','.join(['?']*len(codes))})
            ORDER BY ym DESC
        """, conn_emp, params=codes)
        conn_emp.close()

        # 수출 데이터
        conn_trade = sqlite3.connect(TRADE_DB_PATH)
        trade_map = pd.read_sql_query(f"""
            SELECT stock_code, hs_code
            FROM hs_code_company_map
            WHERE stock_code IN ({','.join(['?']*len(codes))})
        """, conn_trade, params=codes)
        
        hs_list = trade_map['hs_code'].unique().tolist()
        trade_series = pd.DataFrame()
        if hs_list:
            trade_series = pd.read_sql_query(f"""
                SELECT hs_code, period_ym, export_value
                FROM trade_series_cache
                WHERE hs_code IN ({','.join(['?']*len(hs_list))})
                ORDER BY period_ym DESC
            """, conn_trade, params=hs_list)
        conn_trade.close()

        results = []
        for stock in stocks:
            code = stock['stock_code']
            
            cond1, cond2, cond3, cond4 = False, False, False, False
            details = {}

            # 재무/감가상각 (Condition 1 & 2)
            s_fin = financials[financials['stock_code'] == code]
            rev = 0
            if not s_fin.empty:
                latest_fin = s_fin.iloc[0]
                rev = latest_fin['revenue'] or 0
                op = latest_fin['operating_profit'] or 0
                if rev > 0:
                    opm = op / rev
                    if op < 0 or opm <= opm_threshold:
                        cond2 = True
                        details['opm'] = round(opm * 100, 2)
                elif op < 0:
                    cond2 = True

                depr = latest_fin['depreciation_amortization'] or 0
                if depr == 0:
                    s_cf = cash_flows[cash_flows['stock_code'] == code]
                    if not s_cf.empty:
                        depr = s_cf.iloc[0]['depreciation'] or 0
                        if len(s_cf) > 1:
                            r1, r2 = s_cf.iloc[0], s_cf.iloc[1]
                            if r1['year'] == r2['year'] and r1['quarter'] > r2['quarter']:
                                depr = (r1['depreciation'] or 0) - (r2['depreciation'] or 0)
                
                if depr > 0 and rev > 0:
                    depr_ratio = depr / rev
                    if depr_ratio >= depr_threshold:
                        cond1 = True
                        details['depr_ratio'] = round(depr_ratio * 100, 1)

            # 고용 (Condition 3)
            s_emp = emp_data[emp_data['stock_code'] == code]
            if not s_emp.empty:
                latest_w = s_emp.iloc[0]['worker_count'] or 0
                if len(s_emp) > emp_months:
                    prev_w = s_emp.iloc[emp_months]['worker_count'] or 0
                    if latest_w > prev_w:
                        cond3 = True
                        details['emp_inc'] = latest_w - prev_w

            # 수출 (Condition 4)
            s_map = trade_map[trade_map['stock_code'] == code]
            if not s_map.empty and not trade_series.empty:
                hs_codes = s_map['hs_code'].unique()
                s_trade = trade_series[trade_series['hs_code'].isin(hs_codes)]
                if not s_trade.empty:
                    periodic = s_trade.groupby('period_ym')['export_value'].sum().sort_index(ascending=False)
                    if len(periodic) > 1:
                        if periodic.iloc[0] > periodic.iloc[1]:
                            cond4 = True
                            details['export_inc_pct'] = round((periodic.iloc[0] - periodic.iloc[1]) / periodic.iloc[1] * 100, 1) if periodic.iloc[1] > 0 else 100

            score = sum([cond1, cond2, cond3, cond4])
            if score >= min_score:
                # 최신 주가 정보 보완
                price, chg_pct = None, None
                p_row = conn_stock.execute("SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 2", (code,)).fetchall()
                if p_row:
                    price = p_row[0][0]
                    if len(p_row) > 1:
                        chg_pct = (price - p_row[1][0]) / p_row[1][0] * 100
                
                results.append({
                    "stock_code": code,
                    "stock_name": stock['stock_name'],
                    "score": score,
                    "cond1": cond1,
                    "cond2": cond2,
                    "cond3": cond3,
                    "cond4": cond4,
                    "details": details,
                    "price": price,
                    "chg_pct": chg_pct
                })

        results.sort(key=lambda x: x['score'], reverse=True)
        return _json_safe({"stocks": results})

    except Exception as e:
        logger.error(f"Error in special-filter: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn_stock.close()
