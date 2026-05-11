"""
migrate_report_type.py — 연결/별도 구분 + 외부소스 스냅샷 테이블 마이그레이션

변경사항:
  1. financial_data에 report_type 컬럼 추가 (기존 데이터 → 'CFS', 별도 신규 → 'OFS')
  2. cash_flow_data에 report_type 컬럼 추가
  3. financial_source_snapshot 테이블 신규 생성
     - FnGuide/Naver 원본 수치를 보존
     - 두 번째 AI가 독립 검증할 수 있는 감사 테이블
     - verified_by 필드: AI 검증자 기록

실행:
    python3 scripts/migrate_report_type.py
    python3 scripts/migrate_report_type.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)
DB_PATH = Path(__file__).resolve().parent.parent / "stock.db"


def col_exists(cur, table: str, col: str) -> bool:
    cur.execute(f"PRAGMA table_info({table})")
    return any(r[1] == col for r in cur.fetchall())


def table_exists(cur, table: str) -> bool:
    return cur.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()[0] > 0


def run(dry_run: bool) -> None:
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()

    steps: list[tuple[str, str]] = []   # (label, sql)

    # ── 1. financial_data.report_type ─────────────────────────
    if not col_exists(cur, "financial_data", "report_type"):
        steps.append((
            "financial_data.report_type 추가",
            "ALTER TABLE financial_data ADD COLUMN report_type TEXT DEFAULT 'CFS'"
        ))
        steps.append((
            "기존 financial_data → CFS 마킹",
            "UPDATE financial_data SET report_type='CFS' WHERE report_type IS NULL"
        ))
    else:
        logger.info("financial_data.report_type 이미 존재")

    # ── 2. cash_flow_data.report_type ─────────────────────────
    if not col_exists(cur, "cash_flow_data", "report_type"):
        steps.append((
            "cash_flow_data.report_type 추가",
            "ALTER TABLE cash_flow_data ADD COLUMN report_type TEXT DEFAULT 'CFS'"
        ))
        steps.append((
            "기존 cash_flow_data → CFS 마킹",
            "UPDATE cash_flow_data SET report_type='CFS' WHERE report_type IS NULL"
        ))
    else:
        logger.info("cash_flow_data.report_type 이미 존재")

    # ── 3. financial_source_snapshot 신규 테이블 ──────────────
    if not table_exists(cur, "financial_source_snapshot"):
        steps.append((
            "financial_source_snapshot 테이블 생성",
            """
CREATE TABLE financial_source_snapshot (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code      TEXT    NOT NULL,
    year            INTEGER NOT NULL,
    quarter         INTEGER NOT NULL DEFAULT 0,
    is_annual       INTEGER NOT NULL DEFAULT 1,
    report_type     TEXT    NOT NULL DEFAULT 'CFS',  -- CFS=연결, OFS=별도
    data_source     TEXT    NOT NULL,               -- fnguide, naver, dart, kis
    source_url      TEXT,
    fetched_at      TEXT    NOT NULL,               -- ISO8601
    -- 손익계산서 (단위: 원)
    revenue             REAL,
    operating_profit    REAL,
    net_income          REAL,
    eps                 REAL,
    bps                 REAL,
    dps                 REAL,
    -- 재무상태표 (단위: 원)
    total_assets        REAL,
    total_liabilities   REAL,
    total_equity        REAL,
    -- 현금흐름표 (단위: 원)
    operating_cf        REAL,
    investing_cf        REAL,
    financing_cf        REAL,
    capex               REAL,
    -- AI 검증 필드
    verified_by         TEXT,   -- 검증한 AI 모델명
    verified_at         TEXT,
    verification_status TEXT,   -- match / mismatch / unverified
    verification_note   TEXT,
    -- 감사 추적
    raw_data_json       TEXT,   -- 원본 파싱 결과 (JSON 문자열)
    UNIQUE(stock_code, year, quarter, is_annual, report_type, data_source, fetched_at)
)
            """,
        ))
        steps.append((
            "financial_source_snapshot 인덱스",
            "CREATE INDEX IF NOT EXISTS ix_fss_code_year ON financial_source_snapshot(stock_code, year)"
        ))
        steps.append((
            "financial_source_snapshot 소스 인덱스",
            "CREATE INDEX IF NOT EXISTS ix_fss_source ON financial_source_snapshot(data_source, fetched_at)"
        ))
    else:
        logger.info("financial_source_snapshot 이미 존재")

    # ── 실행 ───────────────────────────────────────────────────
    if not steps:
        logger.info("변경사항 없음 — 이미 최신 스키마")
        conn.close()
        return

    for label, sql in steps:
        logger.info(f"{'[DRY-RUN] ' if dry_run else ''}실행: {label}")
        if not dry_run:
            try:
                conn.executescript(sql) if "CREATE" in sql else conn.execute(sql)
                conn.commit()
            except Exception as e:
                logger.error(f"오류: {e}")
                raise

    if not dry_run:
        # 검증
        cur.execute("PRAGMA table_info(financial_data)")
        fd_cols = [r[1] for r in cur.fetchall()]
        cur.execute("PRAGMA table_info(cash_flow_data)")
        cf_cols = [r[1] for r in cur.fetchall()]
        has_snapshot = table_exists(cur, "financial_source_snapshot")

        cfs_cnt = conn.execute(
            "SELECT COUNT(*) FROM financial_data WHERE report_type='CFS'"
        ).fetchone()[0]

        logger.info(f"[완료] financial_data report_type 존재: {'report_type' in fd_cols}")
        logger.info(f"[완료] cash_flow_data report_type 존재: {'report_type' in cf_cols}")
        logger.info(f"[완료] financial_source_snapshot 존재: {has_snapshot}")
        logger.info(f"[완료] CFS 마킹된 financial_data 행: {cfs_cnt:,}")

    conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    run(args.dry_run)


if __name__ == "__main__":
    main()
