from fastapi import APIRouter, BackgroundTasks, HTTPException
import sqlite3
import os
from typing import List, Optional, Dict
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

DB_PATH = "stock.db"

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
