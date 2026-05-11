"""
텔레그램 데이터를 100% 신뢰하여 DB를 재구축하는 스크립트.

1. hs_code_company_map → telegram_company_hs_flow_map 기반으로 완전 재구축
2. hs_company_market_share 테이블 생성 + 주요 반도체 시장비율 추가
3. 누락된 hs_sector_map 항목 추가 (DRAM 모듈, OLED, MLCC 등)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from datetime import datetime

ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = ROOT_DIR / "data" / "hs_trade_lab.db"

NOW = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")
    return conn


def step1_rebuild_hs_code_company_map(conn: sqlite3.Connection) -> None:
    """telegram_company_hs_flow_map → hs_code_company_map 완전 재구축."""
    print("Step 1: hs_code_company_map 재구축 시작...")

    # 기존 테이블 보존 (백업)
    conn.execute("DROP TABLE IF EXISTS hs_code_company_map_backup_telegram")
    conn.execute("""
        CREATE TABLE hs_code_company_map_backup_telegram AS
        SELECT * FROM hs_code_company_map
    """)
    backup_cnt = conn.execute(
        "SELECT COUNT(*) FROM hs_code_company_map_backup_telegram"
    ).fetchone()[0]
    print(f"  기존 {backup_cnt}건 백업 완료 → hs_code_company_map_backup_telegram")

    # 텔레그램 기반으로 재구축
    # flow_type: telegram은 export/import 구분이 되어 있음
    conn.execute("DELETE FROM hs_code_company_map")
    conn.execute("""
        INSERT INTO hs_code_company_map (
            hs_code, hs_name, stock_code, stock_name,
            sector_name, match_type, confidence,
            mapping_status, flow_type, note, created_at, updated_at
        )
        SELECT
            hs_code,
            COALESCE(NULLIF(hs_name, ''), hs_code) AS hs_name,
            stock_code,
            stock_name,
            COALESCE(NULLIF(flow_scope, ''), '') AS sector_name,
            'telegram' AS match_type,
            COALESCE(confidence, 1.0) AS confidence,
            COALESCE(NULLIF(mapping_status, ''), 'exact') AS mapping_status,
            COALESCE(NULLIF(flow_type, ''), 'export') AS flow_type,
            'telegram:' || COALESCE(source_note, ''),
            ?,
            ?
        FROM (
            SELECT
                hs_code,
                MAX(hs_name) AS hs_name,
                stock_code,
                MAX(stock_name) AS stock_name,
                MAX(flow_scope) AS flow_scope,
                MAX(mapping_status) AS mapping_status,
                CASE
                  WHEN SUM(CASE WHEN flow_type='export' THEN 1 ELSE 0 END) >=
                       SUM(CASE WHEN flow_type='import' THEN 1 ELSE 0 END)
                  THEN 'export' ELSE 'import'
                END AS flow_type,
                MAX(source_note) AS source_note,
                MAX(COALESCE(confidence, 1.0)) AS confidence
            FROM telegram_company_hs_flow_map
            WHERE stock_code IS NOT NULL
              AND hs_code IS NOT NULL
              AND TRIM(stock_code) != ''
              AND TRIM(hs_code) != ''
            GROUP BY hs_code, stock_code
        ) t
    """, (NOW, NOW))
    new_cnt = conn.execute("SELECT COUNT(*) FROM hs_code_company_map").fetchone()[0]
    print(f"  신규 {new_cnt}건 삽입 완료 (텔레그램 기반)")
    conn.commit()


def step2_create_market_share_table(conn: sqlite3.Connection) -> None:
    """hs_company_market_share 테이블 생성 및 주요 시장비율 데이터 추가."""
    print("Step 2: hs_company_market_share 테이블 구축...")

    conn.execute("DROP TABLE IF EXISTS hs_company_market_share")
    conn.execute("""
        CREATE TABLE hs_company_market_share (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hs_code TEXT NOT NULL,
            hs_name TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT NOT NULL,
            share_pct REAL NOT NULL CHECK(share_pct > 0 AND share_pct <= 1.0),
            basis TEXT NOT NULL DEFAULT 'market_share',
            source_note TEXT,
            as_of_year INTEGER,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_hs_market_share
        ON hs_company_market_share (hs_code, stock_code)
    """)

    # ─── 주요 반도체 메모리 시장비율 (2024-2025 기준 한국 수출 추정) ───
    # 근거: 사업보고서, 시장조사기관 (TrendForce, IDC) 데이터 기반
    #
    # DRAM 글로벌 점유율(2024): Samsung ~44%, SK하이닉스 ~29%, Micron ~22%
    # 한국 내 비율: Samsung 60%, SK하이닉스 40%
    #
    # NAND 글로벌(2024): Samsung ~31%, Kioxia ~20%, WD ~15%, SK하이닉스 ~17%
    # 한국 내 비율: Samsung 65%, SK하이닉스 35%
    #
    # SSD 글로벌(2024 엔터프라이즈): Samsung ~35%, SK하이닉스 ~20%, Micron ~25%
    # 한국 내 비율: Samsung 63%, SK하이닉스 37%
    #
    # HBM 글로벌(2024): SK하이닉스 ~55%, Samsung ~32%, Micron ~13%
    # 한국 내 비율: SK하이닉스 63%, Samsung 37%
    #
    # 스마트폰(8517130000): Samsung Electronics 100% (SK하이닉스는 미해당)
    # OLED(8524911000, 8528725000): Samsung Display 관련 - Samsung Electronics 귀속

    shares = [
        # DRAM (8542321010)
        ("8542321010", "DRAM", "005930", "삼성전자", 0.60, "TrendForce 2024, 한국 수출 비율 추정", 2024),
        ("8542321010", "DRAM", "000660", "SK하이닉스", 0.40, "TrendForce 2024, 한국 수출 비율 추정", 2024),
        # DRAM 모듈 (8473304060)
        ("8473304060", "DRAM 모듈", "005930", "삼성전자", 0.60, "시장점유율 기반 추정", 2024),
        ("8473304060", "DRAM 모듈", "000660", "SK하이닉스", 0.40, "시장점유율 기반 추정", 2024),
        # NAND (8542321030)
        ("8542321030", "NAND", "005930", "삼성전자", 0.65, "TrendForce 2024, 한국 수출 비율 추정", 2024),
        ("8542321030", "NAND", "000660", "SK하이닉스", 0.35, "TrendForce 2024 (Solidigm 포함)", 2024),
        # SSD (8523511000)
        ("8523511000", "SSD", "005930", "삼성전자", 0.63, "IDC 2024 SSD 시장, 한국 수출 비율 추정", 2024),
        ("8523511000", "SSD", "000660", "SK하이닉스", 0.37, "IDC 2024 SSD 시장 (Solidigm 포함)", 2024),
        # HBM / 복합칩 (8542323000)
        ("8542323000", "복합칩·HBM", "000660", "SK하이닉스", 0.63, "TrendForce 2024 HBM 점유율, SK하이닉스 우위", 2024),
        ("8542323000", "복합칩·HBM", "005930", "삼성전자", 0.37, "TrendForce 2024 HBM 점유율", 2024),
        # 전자집적회로 메모리 통합 (854232) - 상위 코드, DRAM+NAND 혼합
        ("854232", "전자집적회로: 메모리", "005930", "삼성전자", 0.58, "메모리 전체 점유율 가중 평균 추정", 2024),
        ("854232", "전자집적회로: 메모리", "000660", "SK하이닉스", 0.42, "메모리 전체 점유율 가중 평균 추정", 2024),
    ]

    conn.executemany("""
        INSERT INTO hs_company_market_share (
            hs_code, hs_name, stock_code, stock_name, share_pct,
            basis, source_note, as_of_year, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [(h, n, sc, sn, sp, b, b, yr, NOW) for h, n, sc, sn, sp, b, yr in shares])

    cnt = conn.execute("SELECT COUNT(*) FROM hs_company_market_share").fetchone()[0]
    print(f"  {cnt}건 시장비율 데이터 추가 완료")
    conn.commit()


def step3_add_hs_sector_map_entries(conn: sqlite3.Connection) -> None:
    """텔레그램 Telegram에 있으나 hs_sector_map에 없는 중요 HS 코드 추가."""
    print("Step 3: hs_sector_map 누락 항목 추가...")

    # hs_sector_map 스키마 확인
    cols = [r[1] for r in conn.execute("PRAGMA table_info(hs_sector_map)").fetchall()]
    print(f"  hs_sector_map 컬럼: {cols}")

    new_entries = [
        # semiconductors 섹터
        ("8473304060", "DRAM 모듈", "DRAM 모듈", "semiconductors", "exact"),
        ("8524911000", "유기발광다이오드 표시 모듈", "OLED 표시 모듈", "semiconductors", "exact"),
        ("8528725000", "유기발광다이오드(오엘이디) 방식 TV", "OLED TV/디스플레이", "semiconductors", "exact"),
        ("8528521000", "평판디스플레이 모니터", "평판디스플레이", "semiconductors", "exact"),
        ("8532240000", "세라믹 유전체의 것(다층)", "MLCC", "semiconductors", "exact"),
        ("8421219020", "반도체 제조용 여과기나 청정기", "반도체 제조용 여과기", "semiconductors", "exact"),
        ("8464202000", "반도체 웨이퍼 연마기", "반도체 웨이퍼 연마기", "semiconductors", "exact"),
        ("3405901000", "반도체 웨이퍼 연마용 조제품", "웨이퍼 연마 조제품", "semiconductors", "exact"),
        # energy_materials 섹터
        ("2847000000", "과산화수소", "과산화수소", "energy_materials", "exact"),
    ]

    added = 0
    for hs_code, hs_name, display_name, sector_key, mapping_status in new_entries:
        # 이미 있으면 스킵
        existing = conn.execute(
            "SELECT 1 FROM hs_sector_map WHERE hs_code = ? AND sector_key = ?",
            (hs_code, sector_key)
        ).fetchone()
        if existing:
            continue

        # 실제 존재하는 컬럼에만 삽입
        if "display_name" in cols and "mapping_status" in cols:
            conn.execute("""
                INSERT OR IGNORE INTO hs_sector_map
                    (hs_code, hs_name, display_name, sector_key, mapping_status)
                VALUES (?, ?, ?, ?, ?)
            """, (hs_code, hs_name, display_name, sector_key, mapping_status))
        elif "display_name" in cols:
            conn.execute("""
                INSERT OR IGNORE INTO hs_sector_map
                    (hs_code, hs_name, display_name, sector_key)
                VALUES (?, ?, ?, ?)
            """, (hs_code, hs_name, display_name, sector_key))
        else:
            conn.execute("""
                INSERT OR IGNORE INTO hs_sector_map (hs_code, hs_name, sector_key)
                VALUES (?, ?, ?)
            """, (hs_code, hs_name, sector_key))
        added += 1

    conn.commit()
    print(f"  {added}건 신규 sector_map 추가 완료")


def step4_add_share_pct_to_map(conn: sqlite3.Connection) -> None:
    """hs_code_company_map에 market_share_pct 컬럼 추가."""
    print("Step 4: hs_code_company_map에 market_share_pct 컬럼 추가...")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(hs_code_company_map)").fetchall()]
    if "market_share_pct" not in cols:
        conn.execute("ALTER TABLE hs_code_company_map ADD COLUMN market_share_pct REAL DEFAULT NULL")
        print("  market_share_pct 컬럼 추가됨")
    else:
        print("  market_share_pct 컬럼 이미 존재")

    # market_share_pct 업데이트
    conn.execute("""
        UPDATE hs_code_company_map
        SET market_share_pct = (
            SELECT ms.share_pct
            FROM hs_company_market_share ms
            WHERE ms.hs_code = hs_code_company_map.hs_code
              AND ms.stock_code = hs_code_company_map.stock_code
            LIMIT 1
        )
        WHERE EXISTS (
            SELECT 1 FROM hs_company_market_share ms
            WHERE ms.hs_code = hs_code_company_map.hs_code
              AND ms.stock_code = hs_code_company_map.stock_code
        )
    """)
    updated = conn.execute(
        "SELECT COUNT(*) FROM hs_code_company_map WHERE market_share_pct IS NOT NULL"
    ).fetchone()[0]
    print(f"  {updated}건에 market_share_pct 적용됨")
    conn.commit()


def print_summary(conn: sqlite3.Connection) -> None:
    print()
    print("=" * 60)
    print("최종 현황:")
    for tbl in ["hs_code_company_map", "hs_company_market_share", "hs_sector_map"]:
        cnt = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        print(f"  {tbl}: {cnt}건")
    # 시장비율 적용된 주요 HS 코드
    rows = conn.execute("""
        SELECT hs_code, hs_name, stock_name, market_share_pct
        FROM hs_code_company_map
        WHERE market_share_pct IS NOT NULL
        ORDER BY hs_code, market_share_pct DESC
    """).fetchall()
    print()
    print("  시장비율 적용 현황:")
    for r in rows:
        print(f"    {r['hs_code']} {r['hs_name']} | {r['stock_name']}: {r['market_share_pct']*100:.0f}%")


if __name__ == "__main__":
    conn = connect()
    try:
        step1_rebuild_hs_code_company_map(conn)
        step2_create_market_share_table(conn)
        step3_add_hs_sector_map_entries(conn)
        step4_add_share_pct_to_map(conn)
        print_summary(conn)
        print()
        print("완료! rebuild_analysis2_cache.py를 실행하여 캐시를 재빌드하세요.")
    finally:
        conn.close()
