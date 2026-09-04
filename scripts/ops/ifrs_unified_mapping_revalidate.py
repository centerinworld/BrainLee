#!/usr/bin/env python3
"""
IFRS 단일화 규칙/매핑 카탈로그 생성 + 재검증 실행

1) IFRS 필드 규칙 테이블 생성/업서트
2) DART raw account(account_id/account_nm/sj_nm) -> canonical field 매핑 카탈로그 생성
3) 외부소스(Naver/FnGuide) 필드 매핑 규칙 등록
4) 커버리지 스냅샷 생성
5) run_daily_validation.sh 재실행
"""
from __future__ import annotations

import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

DB = Path('/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db')
ROOT = Path('/Volumes/Realtek_NVME/stock_dashboard/runtime')

CANONICAL_RULES = [
    # field, statement, ifrs_basis, quarter_transform, stock_flow, notes
    ('revenue', 'IS', 'IFRS 수익(영업수익/이자수익/보험수익 업종별 상이)', '누적분기->단일분기 변환 적용', 'FLOW', '금융업은 매출 정의 차이 발생 가능'),
    ('operating_profit', 'IS', 'IFRS 영업이익', '누적분기->단일분기 변환 적용', 'FLOW', '일회성 손익/분류변경 영향 가능'),
    ('net_income', 'IS', 'IFRS 당기순이익(지배/비지배 귀속 구분 주의)', '누적분기->단일분기 변환 적용', 'FLOW', '지배주주귀속 순이익과 혼재 가능'),
    ('total_assets', 'BS', 'IFRS 자산총계(기간말)', '변환 없음(기간말 스톡값 직접 사용)', 'STOCK', '연결/별도 기준 혼입 금지'),
    ('total_equity', 'BS', 'IFRS 자본총계(기간말)', '변환 없음(기간말 스톡값 직접 사용)', 'STOCK', '지배기업소유주지분과 구분 필요'),
]

SOURCE_RULES = [
    # source, source_field, canonical_field, report_type_scope, transform_rule, priority, verification_rule, active
    ('DART', 'account_nm/account_id', 'revenue', 'CFS,OFS', '누적분기는 단일분기로 환산', 1, '원천우선', 1),
    ('DART', 'account_nm/account_id', 'operating_profit', 'CFS,OFS', '누적분기는 단일분기로 환산', 1, '원천우선', 1),
    ('DART', 'account_nm/account_id', 'net_income', 'CFS,OFS', '누적분기는 단일분기로 환산', 1, '원천우선', 1),
    ('DART', 'account_nm/account_id', 'total_assets', 'CFS,OFS', '기간말값 직접사용', 1, '원천우선', 1),
    ('DART', 'account_nm/account_id', 'total_equity', 'CFS,OFS', '기간말값 직접사용', 1, '원천우선', 1),
    ('NAVER', 'revenue', 'revenue', 'CFS(주로)', '소스값 사용(검증시 DART와 허용오차 비교)', 2, 'DART 교차검증', 1),
    ('NAVER', 'operating_profit', 'operating_profit', 'CFS(주로)', '소스값 사용(검증시 DART와 허용오차 비교)', 2, 'DART 교차검증', 1),
    ('NAVER', 'net_income', 'net_income', 'CFS(주로)', '소스값 사용(검증시 DART와 허용오차 비교)', 2, 'DART 교차검증', 1),
    ('NAVER', 'total_assets', 'total_assets', 'CFS(주로)', '기간말값 사용', 2, 'DART 교차검증', 1),
    ('NAVER', 'total_equity', 'total_equity', 'CFS(주로)', '기간말값 사용', 2, 'DART 교차검증', 1),
    ('FNGUIDE', 'revenue', 'revenue', 'CFS/OFS 혼재 가능', '소스값 사용(검증시 DART와 허용오차 비교)', 2, 'DART 교차검증', 1),
    ('FNGUIDE', 'operating_profit', 'operating_profit', 'CFS/OFS 혼재 가능', '소스값 사용(검증시 DART와 허용오차 비교)', 2, 'DART 교차검증', 1),
    ('FNGUIDE', 'net_income', 'net_income', 'CFS/OFS 혼재 가능', '소스값 사용(검증시 DART와 허용오차 비교)', 2, 'DART 교차검증', 1),
    ('FNGUIDE', 'total_assets', 'total_assets', 'CFS/OFS 혼재 가능', '기간말값 사용', 2, 'DART 교차검증', 1),
    ('FNGUIDE', 'total_equity', 'total_equity', 'CFS/OFS 혼재 가능', '기간말값 사용', 2, 'DART 교차검증', 1),
]

DART_PATTERNS = {
    'revenue': [
        '매출', '영업수익', '수익(매출액)', 'Revenue', '이자수익', '보험수익',
    ],
    'operating_profit': [
        '영업이익', '영업이익(손실)', 'OperatingIncomeLoss',
    ],
    'net_income': [
        '당기순이익', '당기순이익(손실)', '순이익', 'Profit(Loss)',
    ],
    'total_assets': [
        '자산총계', '총자산', 'AssetsTotal',
    ],
    'total_equity': [
        '자본총계', '총자본', 'EquityTotal', '지배기업소유주지분',
    ],
}


def connect():
    conn = sqlite3.connect(DB, timeout=300)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=300000')
    return conn


def ensure_tables(conn: sqlite3.Connection):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ifrs_field_rules (
            field TEXT PRIMARY KEY,
            statement_type TEXT NOT NULL,
            ifrs_basis TEXT NOT NULL,
            quarter_transform_rule TEXT NOT NULL,
            stock_flow_type TEXT NOT NULL,
            notes TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS source_field_mapping_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT NOT NULL,
            source_field TEXT NOT NULL,
            canonical_field TEXT NOT NULL,
            report_type_scope TEXT,
            transform_rule TEXT,
            source_priority INTEGER DEFAULT 9,
            verification_rule TEXT,
            active INTEGER DEFAULT 1,
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(source_name, source_field, canonical_field)
        );

        CREATE TABLE IF NOT EXISTS dart_item_mapping_catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_field TEXT NOT NULL,
            account_id TEXT,
            account_nm TEXT NOT NULL,
            sj_nm TEXT,
            fs_div TEXT,
            match_rule TEXT,
            sample_count INTEGER DEFAULT 0,
            first_year INTEGER,
            last_year INTEGER,
            confidence TEXT DEFAULT 'MEDIUM',
            active INTEGER DEFAULT 1,
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(canonical_field, account_id, account_nm, sj_nm, fs_div)
        );

        CREATE TABLE IF NOT EXISTS ifrs_mapping_coverage_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            source_name TEXT NOT NULL,
            field TEXT NOT NULL,
            year INTEGER,
            total_rows INTEGER,
            non_null_rows INTEGER,
            coverage_pct REAL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()


def upsert_rules(conn: sqlite3.Connection):
    for row in CANONICAL_RULES:
        conn.execute(
            """
            INSERT INTO ifrs_field_rules(field, statement_type, ifrs_basis, quarter_transform_rule, stock_flow_type, notes, updated_at)
            VALUES(?,?,?,?,?,?,datetime('now'))
            ON CONFLICT(field) DO UPDATE SET
              statement_type=excluded.statement_type,
              ifrs_basis=excluded.ifrs_basis,
              quarter_transform_rule=excluded.quarter_transform_rule,
              stock_flow_type=excluded.stock_flow_type,
              notes=excluded.notes,
              updated_at=datetime('now')
            """,
            row,
        )

    for row in SOURCE_RULES:
        conn.execute(
            """
            INSERT INTO source_field_mapping_rules(source_name, source_field, canonical_field, report_type_scope, transform_rule, source_priority, verification_rule, active, updated_at)
            VALUES(?,?,?,?,?,?,?,?,datetime('now'))
            ON CONFLICT(source_name, source_field, canonical_field) DO UPDATE SET
              report_type_scope=excluded.report_type_scope,
              transform_rule=excluded.transform_rule,
              source_priority=excluded.source_priority,
              verification_rule=excluded.verification_rule,
              active=excluded.active,
              updated_at=datetime('now')
            """,
            row,
        )
    conn.commit()


def refresh_dart_mapping_catalog(conn: sqlite3.Connection):
    for field, pats in DART_PATTERNS.items():
        q = " OR ".join(["account_nm LIKE ?" for _ in pats])
        params = []
        for p in pats:
            p_clean = p.replace("\\", "")
            params.append(f"%{p_clean}%")
        rows = conn.execute(
            f"""
            SELECT
              COALESCE(account_id,'') as account_id,
              account_nm,
              COALESCE(sj_nm,'') as sj_nm,
              COALESCE(fs_div,'') as fs_div,
              COUNT(*) as cnt,
              MIN(year) as min_year,
              MAX(year) as max_year
            FROM dart_raw_accounts
            WHERE ({q})
            GROUP BY 1,2,3,4
            """,
            params,
        ).fetchall()

        for r in rows:
            account_id, account_nm, sj_nm, fs_div, cnt, min_year, max_year = r
            confidence = 'HIGH' if cnt >= 50 else ('MEDIUM' if cnt >= 10 else 'LOW')
            conn.execute(
                """
                INSERT INTO dart_item_mapping_catalog(
                  canonical_field, account_id, account_nm, sj_nm, fs_div,
                  match_rule, sample_count, first_year, last_year, confidence, active, updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,1,datetime('now'))
                ON CONFLICT(canonical_field, account_id, account_nm, sj_nm, fs_div) DO UPDATE SET
                  match_rule=excluded.match_rule,
                  sample_count=excluded.sample_count,
                  first_year=excluded.first_year,
                  last_year=excluded.last_year,
                  confidence=excluded.confidence,
                  active=1,
                  updated_at=datetime('now')
                """,
                (
                    field,
                    account_id if account_id else None,
                    account_nm,
                    sj_nm if sj_nm else None,
                    fs_div if fs_div else None,
                    'PATTERN_MATCH',
                    cnt,
                    min_year,
                    max_year,
                    confidence,
                ),
            )
    conn.commit()


def snapshot_coverage(conn: sqlite3.Connection, run_id: str):
    conn.execute("DELETE FROM ifrs_mapping_coverage_snapshot WHERE run_id=?", (run_id,))

    years = [r[0] for r in conn.execute("SELECT DISTINCT year FROM financial_data WHERE year BETWEEN 2016 AND 2026 ORDER BY year")]
    fields = ['revenue', 'operating_profit', 'net_income', 'total_assets', 'total_equity']

    for y in years:
        for f in fields:
            total, non_null = conn.execute(
                f"""
                SELECT COUNT(*), SUM(CASE WHEN {f} IS NOT NULL THEN 1 ELSE 0 END)
                FROM financial_data
                WHERE year=? AND is_annual=0
                """,
                (y,),
            ).fetchone()
            cov = (non_null or 0) * 100.0 / total if total else 0.0
            conn.execute(
                """
                INSERT INTO ifrs_mapping_coverage_snapshot(run_id, source_name, field, year, total_rows, non_null_rows, coverage_pct)
                VALUES(?,?,?,?,?,?,?)
                """,
                (run_id, 'FINANCIAL_DATA', f, y, total or 0, non_null or 0, cov),
            )

        for f in fields:
            total, non_null = conn.execute(
                f"""
                SELECT COUNT(*), SUM(CASE WHEN {f} IS NOT NULL THEN 1 ELSE 0 END)
                FROM naver_financial
                WHERE year=? AND is_annual=0
                """,
                (y,),
            ).fetchone()
            cov = (non_null or 0) * 100.0 / total if total else 0.0
            conn.execute(
                """
                INSERT INTO ifrs_mapping_coverage_snapshot(run_id, source_name, field, year, total_rows, non_null_rows, coverage_pct)
                VALUES(?,?,?,?,?,?,?)
                """,
                (run_id, 'NAVER', f, y, total or 0, non_null or 0, cov),
            )

    conn.commit()


def run_validation_pipeline():
    subprocess.run(['bash', str(ROOT / 'scratch' / 'run_daily_validation.sh')], check=True)


def main():
    run_id = f"ifrs_unify_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    conn = connect()
    try:
        ensure_tables(conn)
        upsert_rules(conn)
        refresh_dart_mapping_catalog(conn)
        snapshot_coverage(conn, run_id)
    finally:
        conn.close()

    run_validation_pipeline()
    print(f"DONE run_id={run_id}")


if __name__ == '__main__':
    main()
