"""
employment_monitor/db.py — SQLite 스키마 및 연결
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH       = Path(__file__).parent / "employment.db"
STOCK_DB_PATH = Path(__file__).parent.parent / "stock.db"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _ensure_tables(conn)
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    # ── 업종별 월별 현황 (data.go.kr 집계) ────────────────────────
    conn.execute("""
    CREATE TABLE IF NOT EXISTS employment_monthly (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ym          TEXT NOT NULL,
        sj_gy_fg    TEXT NOT NULL,
        agri_fore_fis        REAL,
        agri_hunt_fore       REAL,
        ahou_gyhwaldong      REAL,
        art_sport_yeoga      REAL,
        bogun_shoe_bokji     REAL,
        budongsanImdae       REAL,
        budongsanImdaeSaeop  REAL,
        edu_service          REAL,
        fis                  REAL,
        fnn_boheom           REAL,
        geonseoleop          REAL,
        inte_foreign_gigwan  REAL,
        jejoeop              REAL,
        jeongi_gas_step      REAL,
        jeongi_gas_tw        REAL,
        jeonmun_sci          REAL,
        minind               REAL,
        publ_aim_brd         REAL,
        pub_hjng_shoe        REAL,
        saeop_siseol_jiwon   REAL,
        sew_smat_rmatrev     REAL,
        sukbak_aeh           REAL,
        unsu                 REAL,
        unsu_changgo_tongsin REAL,
        whol_ret             REAL,
        whol_ret_cons        REAL,
        total                REAL,
        saeopjang_wk_fg      TEXT,
        raw_json             TEXT,
        fetched_at           DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(ym, sj_gy_fg, saeopjang_wk_fg)
    )
    """)

    # ── DART corp_code 매핑 캐시 ───────────────────────────────────
    conn.execute("""
    CREATE TABLE IF NOT EXISTS dart_corp_map (
        stock_code  TEXT PRIMARY KEY,
        corp_code   TEXT NOT NULL,
        corp_name   TEXT,
        mapped_at   DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # ── 상장기업 임직원 월별 현황 ──────────────────────────────────
    conn.execute("""
    CREATE TABLE IF NOT EXISTS employment_company_monthly (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        stock_code      TEXT NOT NULL,
        stock_name      TEXT NOT NULL,
        ym              TEXT NOT NULL,          -- '2024-12'
        employee_count  INTEGER,                -- 임직원 합계
        regular_count   INTEGER,                -- 정규직
        contract_count  INTEGER,                -- 계약직+기타
        is_actual       INTEGER DEFAULT 1,      -- 1=실제(DART), 0=보간
        source          TEXT DEFAULT 'dart',
        company_size    TEXT,                   -- '대기업','중견기업','중소기업'
        mktcap          REAL,                   -- 시가총액(원)
        sector_large    TEXT,
        fetched_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(stock_code, ym)
    )
    """)

    conn.execute("""
    CREATE INDEX IF NOT EXISTS ix_emp_monthly_ym
        ON employment_monthly(ym, sj_gy_fg)
    """)
    conn.execute("""
    CREATE INDEX IF NOT EXISTS ix_emp_company_code
        ON employment_company_monthly(stock_code, ym)
    """)
    conn.execute("""
    CREATE INDEX IF NOT EXISTS ix_emp_company_size
        ON employment_company_monthly(company_size, ym)
    """)
    conn.commit()
