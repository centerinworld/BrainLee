"""
ETF_check DB 초기화
위치: /Applications/stock_dashboard/ETF_check/etf_check.db
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "etf_check.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 일별 ETF 편입금액 테이블
    cur.execute("""
    CREATE TABLE IF NOT EXISTS etf_inclusion_daily (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date  TEXT    NOT NULL,          -- 수집일자 (YYYY-MM-DD)
        stock_code  TEXT    NOT NULL,          -- 종목코드 (6자리)
        stock_name  TEXT,                      -- 종목명
        etf_amount  REAL,                      -- ETF 편입금액(추정) 억원
        current_price REAL,                    -- 현재가 (원)
        market_cap  REAL,                      -- 시가총액 (억원)
        mktcap_ratio REAL,                     -- 시총대비 %
        etf_count   INTEGER,                   -- ETF 검색 수
        is_backfilled INTEGER DEFAULT 0,       -- 1=전일 백필 값 (수집 실패 시)
        collected_at TEXT DEFAULT (datetime('now','localtime')),
        UNIQUE(trade_date, stock_code)         -- 날짜+종목 중복 방지
    )
    """)

    # is_backfilled 컬럼 마이그레이션 (기존 DB에 컬럼 없을 경우)
    try:
        cur.execute("ALTER TABLE etf_inclusion_daily ADD COLUMN is_backfilled INTEGER DEFAULT 0")
        print("[MIGRATE] is_backfilled 컬럼 추가됨")
    except sqlite3.OperationalError:
        pass  # 이미 존재

    # 수집 실행 로그 테이블
    cur.execute("""
    CREATE TABLE IF NOT EXISTS collection_log (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        run_date     TEXT NOT NULL,            -- 실행일자
        started_at   TEXT,                     -- 시작 시각
        finished_at  TEXT,                     -- 완료 시각
        total_stocks INTEGER DEFAULT 0,        -- 전체 종목 수
        success      INTEGER DEFAULT 0,        -- 성공 수
        failed       INTEGER DEFAULT 0,        -- 실패 수
        status       TEXT DEFAULT 'running'    -- running / done / error
    )
    """)

    # 수집 실패 종목 추적 테이블
    cur.execute("""
    CREATE TABLE IF NOT EXISTS collection_failures (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date  TEXT NOT NULL,             -- 수집일자
        stock_code  TEXT NOT NULL,             -- 실패 종목코드
        run_type    TEXT DEFAULT 'main',       -- 'main' / 'retry' (마지막 시도 유형)
        resolved    INTEGER DEFAULT 0,         -- 0=미해결, 1=재수집성공, 2=백필처리
        created_at  TEXT DEFAULT (datetime('now','localtime')),
        updated_at  TEXT DEFAULT (datetime('now','localtime')),
        UNIQUE(trade_date, stock_code)
    )
    """)

    conn.commit()
    conn.close()
    print(f"[OK] DB initialized at: {DB_PATH}")

if __name__ == "__main__":
    init_db()
