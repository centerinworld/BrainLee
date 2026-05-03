import sqlite3
import os

DB_PATH = "/Applications/stock_dashboard/Sector_define/sector_followup.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 블로그 포스트 테이블
    cursor.execute("""
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
    
    # 섹터별 종목 테이블
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sector_stocks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id INTEGER,
        category TEXT, -- Level 1 (DRAM, NAND 등)
        stock_name TEXT, -- Level 2 (삼성전자, SK하이닉스 등)
        stock_code TEXT, -- stock.db 매칭 코드
        ref_price REAL, -- 작성 시점 기준 주가
        memo TEXT,
        FOREIGN KEY(post_id) REFERENCES sector_posts(id)
    )
    """)
    
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
