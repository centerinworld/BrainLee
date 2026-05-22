"""
migrate_base_info.py — A안용 스키마 추가

수행 작업:
  1. stock_universe 컬럼 5개 추가
       isin_code, eng_name, secugrp_nm, kind_stkcert_nm,
       isu_full_name, base_info_updated_at
  2. stock_base_info_history 테이블 생성 (일별 스냅샷)
  3. stock_base_info_changes 테이블 생성 (변동 이벤트)

실행 방법:
    cd /Applications/stock_dashboard
    python3 migrate_base_info.py
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "stock.db"


def col_exists(cur, table, col):
    cur.execute(f"PRAGMA table_info({table})")
    return any(r[1] == col for r in cur.fetchall())


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 1. stock_universe 컬럼 추가
    cols_to_add = [
        ("isin_code",            "VARCHAR(20)"),
        ("eng_name",             "VARCHAR(200)"),
        ("secugrp_nm",           "VARCHAR(50)"),
        ("kind_stkcert_nm",      "VARCHAR(50)"),
        ("isu_full_name",        "VARCHAR(200)"),
        ("base_info_updated_at", "DATETIME"),
    ]
    for col, typ in cols_to_add:
        if not col_exists(cur, "stock_universe", col):
            cur.execute(f"ALTER TABLE stock_universe ADD COLUMN {col} {typ}")
            print(f"[완료] stock_universe.{col} 추가")
        else:
            print(f"[스킵] stock_universe.{col} 이미 존재")

    # 2. stock_base_info_history 일별 스냅샷
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stock_base_info_history (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code    VARCHAR(10) NOT NULL,
            snapshot_date DATE        NOT NULL,
            market_cap    REAL,
            shares_issued INTEGER,
            sector_type   VARCHAR(50),
            secugrp_nm    VARCHAR(50),
            stock_name    VARCHAR(200),
            UNIQUE(stock_code, snapshot_date)
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_base_history_code_date
        ON stock_base_info_history(stock_code, snapshot_date)
    """)
    print("[완료] stock_base_info_history 테이블 + 인덱스")

    # 3. stock_base_info_changes 변동 이벤트
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stock_base_info_changes (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code   VARCHAR(10) NOT NULL,
            change_date  DATE        NOT NULL,
            change_type  VARCHAR(40) NOT NULL,
            old_value    VARCHAR(200),
            new_value    VARCHAR(200),
            description  VARCHAR(300),
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_base_changes_code_date
        ON stock_base_info_changes(stock_code, change_date DESC)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_base_changes_date
        ON stock_base_info_changes(change_date DESC)
    """)
    print("[완료] stock_base_info_changes 테이블 + 인덱스")

    conn.commit()
    conn.close()
    print("\n마이그레이션 완료")


if __name__ == "__main__":
    main()
