"""
employment_monitor/db.py — SQLite 스키마 및 연결
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "employment.db"
STOCK_DB_PATH = Path(__file__).parent.parent / "stock.db"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _ensure_tables(conn)
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS employment_monthly (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ym          TEXT NOT NULL,          -- '2024-03' 형식
        sj_gy_fg    TEXT NOT NULL,          -- '1'=산재, '3'=고용
        agri_fore_fis        REAL,  -- 농업,임업및어업
        agri_hunt_fore       REAL,  -- 농업·수렵업및임업
        ahou_gyhwaldong      REAL,  -- 가구내고용활동
        art_sport_yeoga      REAL,  -- 예술,스포츠및여가
        bogun_shoe_bokji     REAL,  -- 보건및사회복지사업
        budongsanImdae       REAL,  -- 부동산임대및임대업
        budongsanImdaeSaeop  REAL,  -- 부동산임대및사업서비스업
        edu_service          REAL,  -- 교육서비스업
        fis                  REAL,  -- 어업
        fnn_boheom           REAL,  -- 금융및보험업
        geonseoleop          REAL,  -- 건설업
        inte_foreign_gigwan  REAL,  -- 국제및외국기관
        jejoeop              REAL,  -- 제조업
        jeongi_gas_step      REAL,  -- 전기,가스,증기및수도사업
        jeongi_gas_tw        REAL,  -- 전기가스및상수도사업
        jeonmun_sci          REAL,  -- 전문,과학및기술서비스업
        minind               REAL,  -- 광업
        publ_aim_brd         REAL,  -- 출판,영상,방송통신및정보서비스업
        pub_hjng_shoe        REAL,  -- 공공행정,국방및사회보장행정
        saeop_siseol_jiwon   REAL,  -- 사업시설관리및사업지원서비스업
        sew_smat_rmatrev     REAL,  -- 하수,폐기물처리,원료재생및환경복원업
        sukbak_aeh           REAL,  -- 숙박및음식점업
        unsu                 REAL,  -- 운수업
        unsu_changgo_tongsin REAL,  -- 운수,창고및통신업
        whol_ret             REAL,  -- 도매및소매업
        whol_ret_cons        REAL,  -- 도소매및소비자용품수리업
        total                REAL,  -- 전체 합계 (사업장 또는 근로자 수)
        saeopjang_wk_fg      TEXT,  -- '사업장' 또는 '근로자'
        raw_json             TEXT,
        fetched_at           DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(ym, sj_gy_fg, saeopjang_wk_fg)
    );

    CREATE TABLE IF NOT EXISTS employment_company (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        ym           TEXT NOT NULL,
        stock_code   TEXT NOT NULL,
        stock_name   TEXT NOT NULL,
        sector_label TEXT,
        worker_count INTEGER,
        yoy_change   REAL,
        mom_change   REAL,
        fetched_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(ym, stock_code)
    );

    CREATE INDEX IF NOT EXISTS ix_emp_monthly_ym ON employment_monthly(ym, sj_gy_fg);
    CREATE INDEX IF NOT EXISTS ix_emp_company_code ON employment_company(stock_code, ym);
    """)
    conn.commit()
