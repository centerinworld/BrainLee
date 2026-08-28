"""
migrate_insider.py — C안용 스키마 추가

수행 작업:
  1. dart_major_holders 테이블 생성  (대량보유 5%+ 신고)
  2. dart_insider_holdings 테이블 생성 (임원·주요주주 특정증권 소유)

실행 방법:
    cd /Applications/stock_dashboard
    python3 migrate_insider.py
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "stock.db"


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # ── 1) 대량보유상황보고서 (5%+) ────────────────────────────
    # OpenDART /api/majorstock.json 응답 보존
    cur.execute("""
        CREATE TABLE IF NOT EXISTS dart_major_holders (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            rcept_no        VARCHAR(20) NOT NULL,
            rcept_dt        DATE        NOT NULL,
            stock_code      VARCHAR(10) NOT NULL,
            corp_code       VARCHAR(20),
            corp_name       VARCHAR(200),
            repror          VARCHAR(200),         -- 보고자
            stkqy           INTEGER,              -- 보유 주식 수
            stkrt           REAL,                 -- 보유 비율(%)
            ctr_stkqy       INTEGER,              -- 직전 보유 주식
            ctr_stkrt       REAL,                 -- 직전 보유 비율
            report_tp       VARCHAR(50),          -- 보고구분(신규/변동/기타)
            stk_diff        INTEGER,              -- 변동 주식 수(계산값)
            rt_diff         REAL,                 -- 변동 비율(계산값)
            raw_json        TEXT,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(rcept_no, repror, ctr_stkqy)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_major_holders_code_dt ON dart_major_holders(stock_code, rcept_dt DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_major_holders_dt ON dart_major_holders(rcept_dt DESC)")
    print("[완료] dart_major_holders 테이블 + 인덱스")

    # ── 2) 임원·주요주주 특정증권 소유상황 ─────────────────────
    # OpenDART /api/elestock.json 응답 보존
    cur.execute("""
        CREATE TABLE IF NOT EXISTS dart_insider_holdings (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            rcept_no          VARCHAR(20) NOT NULL,
            rcept_dt          DATE        NOT NULL,
            stock_code        VARCHAR(10) NOT NULL,
            corp_code         VARCHAR(20),
            corp_name         VARCHAR(200),
            repror            VARCHAR(200),     -- 보고자(임원명/주주명)
            isu_exctv_rgist   VARCHAR(50),      -- 등기/미등기 (등기임원, 비등기임원)
            isu_exctv_ofcps   VARCHAR(200),     -- 직위 (대표이사 등)
            isu_main_shrholdr VARCHAR(100),     -- 주요주주 구분
            sp_stock_lmp_cnt  INTEGER,          -- 변동후 보유 주식
            sp_stock_lmp_irds_cnt  INTEGER,     -- 변동 주식(+ 매수 / - 매도)
            sp_stock_lmp_irds_rate REAL,        -- 변동 비율
            is_ceo            INTEGER DEFAULT 0,  -- 대표이사 여부 (자체)
            is_significant    INTEGER DEFAULT 0,  -- 주요 변동 여부 (1억원 이상 등, 자체)
            change_amount     REAL,                -- 변동 금액(원, 추정)
            raw_json          TEXT,
            created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(rcept_no, repror, sp_stock_lmp_cnt, sp_stock_lmp_irds_cnt)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_insider_code_dt ON dart_insider_holdings(stock_code, rcept_dt DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_insider_dt ON dart_insider_holdings(rcept_dt DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_insider_ceo_dt ON dart_insider_holdings(is_ceo, rcept_dt DESC) WHERE is_ceo=1")
    print("[완료] dart_insider_holdings 테이블 + 인덱스")

    conn.commit()
    conn.close()
    print("\nC안 마이그레이션 완료")


if __name__ == "__main__":
    main()
