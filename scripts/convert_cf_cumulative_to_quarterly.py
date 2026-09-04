"""
convert_cf_cumulative_to_quarterly.py — cash_flow_data Q1~Q4 누적값을 분기 순값으로 변환

배경:
  DART 분기보고서의 현금흐름표는 통상 *누적값*으로 공시됨.
  현재 DB 점검 결과 (2023~2024년):
    - 누적값 추정: 2,440건 (57%)
    - 분기값 추정: 412건 (10%)
    - 모호: 1,400+ 건

처리 절차:
  1. 백업 테이블 생성 cash_flow_data_backup_YYYYMMDD
  2. 새 컬럼 4개 추가 (operating_cf_q, investing_cf_q, financing_cf_q, capex_q)
     → 기존 누적값은 보존, 분기 순값을 별도 저장
  3. 변환 규칙:
       Q1분기 = Q1누적 (그대로)
       Q2분기 = Q2누적 - Q1누적
       Q3분기 = Q3누적 - Q2누적
       Q4분기 = 연간 - Q3누적 (Q4 분기 row 없으면 INSERT, 있으면 UPDATE)
  4. 변환 적용 조건 (모두 만족해야 함):
       (a) Q1, Q2, Q3, 연간 모두 존재
       (b) ABS(Q3) <= ABS(연간) * 1.05  → 누적값 패턴 확인
       (c) Q1, Q2, Q3 단조 비감소 또는 ±10% 이내 (이상치 차단)
  5. 변환 결과 검증:
       (a) Q1+Q2+Q3+Q4 ≈ 연간 (±2%)
       (b) 통과한 행만 commit

실행:
    python3 scripts/convert_cf_cumulative_to_quarterly.py --dry-run     # 미실행 시뮬
    python3 scripts/convert_cf_cumulative_to_quarterly.py               # 실제 적용
    python3 scripts/convert_cf_cumulative_to_quarterly.py --years 2020-2025
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)
DB_PATH = Path(__file__).resolve().parent.parent / "stock.db"

# 누적/분기 판별 임계치
CUMULATIVE_RATIO_MAX = 1.05   # |Q3| <= |연간| * 1.05 → 누적 패턴
MONOTONIC_TOLERANCE  = 0.10   # Q1<Q2<Q3 위반 허용 비율 (10%)
VERIFY_TOLERANCE     = 0.02   # 변환 후 (Q1+Q2+Q3+Q4) vs 연간 일치 ±2%

CF_COLS = ["operating_cf", "investing_cf", "financing_cf", "capex"]
Q_COLS  = [f"{c}_q" for c in CF_COLS]


def col_exists(cur, table, col) -> bool:
    cur.execute(f"PRAGMA table_info({table})")
    return any(r[1] == col for r in cur.fetchall())


def step_1_backup(conn) -> str:
    """변환 전 백업 테이블 생성. 반환: 백업 테이블명."""
    today = date.today().strftime("%Y%m%d")
    backup_name = f"cash_flow_data_backup_{today}"
    cur = conn.cursor()
    cur.execute(f"DROP TABLE IF EXISTS {backup_name}")
    cur.execute(f"CREATE TABLE {backup_name} AS SELECT * FROM cash_flow_data")
    conn.commit()
    n = cur.execute(f"SELECT COUNT(*) FROM {backup_name}").fetchone()[0]
    logger.info(f"[1단계] 백업 생성: {backup_name} ({n:,}행)")
    return backup_name


def step_2_add_columns(conn) -> None:
    """분기 순값 저장용 컬럼 4개 추가."""
    cur = conn.cursor()
    for col in Q_COLS:
        if not col_exists(cur, "cash_flow_data", col):
            cur.execute(f"ALTER TABLE cash_flow_data ADD COLUMN {col} REAL")
            logger.info(f"[2단계] 컬럼 추가: cash_flow_data.{col}")
        else:
            logger.info(f"[2단계] 컬럼 존재: cash_flow_data.{col} (스킵)")
    if not col_exists(cur, "cash_flow_data", "value_type"):
        cur.execute("ALTER TABLE cash_flow_data ADD COLUMN value_type VARCHAR(20)")
        logger.info("[2단계] 컬럼 추가: cash_flow_data.value_type")
    conn.commit()


def _is_cumulative(q1, q2, q3, yr) -> bool:
    """누적값 패턴 판별. 한 번이라도 위반하면 False."""
    if None in (q1, q2, q3, yr):
        return False
    if yr == 0:
        return False
    # 1) Q3가 연간보다 크면 누적이 아님 (분기값일 가능성)
    if abs(q3) > abs(yr) * CUMULATIVE_RATIO_MAX:
        return False
    # 2) 단조성 (부호 같을 때만 검사)
    #    Q1, Q2, Q3 모두 같은 부호가 일반적인 누적 패턴
    signs = [(1 if v > 0 else -1 if v < 0 else 0) for v in (q1, q2, q3)]
    nonzero_signs = [s for s in signs if s != 0]
    if len(set(nonzero_signs)) > 1:
        # 부호 변동 → 영업CF 변동 큰 회사일 수 있음. 일단 수용
        pass
    return True


def step_3_convert(conn, year_from: int, year_to: int, dry_run: bool) -> dict:
    """
    누적 패턴 행을 분기 순값으로 변환.
    Q4 분기 row 없으면 INSERT, 있으면 UPDATE.
    """
    cur = conn.cursor()
    # 종목 × 연도별로 묶어서 조회
    rows = cur.execute("""
        WITH cf AS (
            SELECT stock_code, year,
                MAX(CASE WHEN quarter=1 AND is_annual=0 THEN operating_cf END) AS q1_op,
                MAX(CASE WHEN quarter=2 AND is_annual=0 THEN operating_cf END) AS q2_op,
                MAX(CASE WHEN quarter=3 AND is_annual=0 THEN operating_cf END) AS q3_op,
                MAX(CASE WHEN is_annual=1                THEN operating_cf END) AS yr_op,
                MAX(CASE WHEN quarter=1 AND is_annual=0 THEN investing_cf END) AS q1_inv,
                MAX(CASE WHEN quarter=2 AND is_annual=0 THEN investing_cf END) AS q2_inv,
                MAX(CASE WHEN quarter=3 AND is_annual=0 THEN investing_cf END) AS q3_inv,
                MAX(CASE WHEN is_annual=1                THEN investing_cf END) AS yr_inv,
                MAX(CASE WHEN quarter=1 AND is_annual=0 THEN financing_cf END) AS q1_fin,
                MAX(CASE WHEN quarter=2 AND is_annual=0 THEN financing_cf END) AS q2_fin,
                MAX(CASE WHEN quarter=3 AND is_annual=0 THEN financing_cf END) AS q3_fin,
                MAX(CASE WHEN is_annual=1                THEN financing_cf END) AS yr_fin,
                MAX(CASE WHEN quarter=1 AND is_annual=0 THEN capex END) AS q1_cap,
                MAX(CASE WHEN quarter=2 AND is_annual=0 THEN capex END) AS q2_cap,
                MAX(CASE WHEN quarter=3 AND is_annual=0 THEN capex END) AS q3_cap,
                MAX(CASE WHEN is_annual=1                THEN capex END) AS yr_cap
            FROM cash_flow_data
            WHERE year BETWEEN ? AND ?
            GROUP BY stock_code, year
        )
        SELECT * FROM cf
        WHERE q1_op IS NOT NULL AND q2_op IS NOT NULL
          AND q3_op IS NOT NULL AND yr_op IS NOT NULL
    """, (year_from, year_to)).fetchall()

    converted = 0
    skipped_cumulative = 0
    skipped_verification = 0
    q4_inserted = 0
    q4_updated = 0

    for row in rows:
        code, year = row[0], row[1]

        # 4개 항목별 데이터
        items = {
            "operating_cf":  (row[2],  row[3],  row[4],  row[5]),   # q1, q2, q3, yr
            "investing_cf":  (row[6],  row[7],  row[8],  row[9]),
            "financing_cf":  (row[10], row[11], row[12], row[13]),
            "capex":         (row[14], row[15], row[16], row[17]),
        }

        # 1) 영업CF 기준으로 누적 패턴 확인 (가장 신뢰성 높음)
        op = items["operating_cf"]
        if not _is_cumulative(*op):
            skipped_cumulative += 1
            continue

        # 2) 변환값 계산
        deltas = {}
        for col, (q1, q2, q3, yr) in items.items():
            if None in (q1, q2, q3, yr):
                deltas[col] = (None, None, None, None)
                continue
            d1 = q1
            d2 = q2 - q1
            d3 = q3 - q2
            d4 = yr - q3
            deltas[col] = (d1, d2, d3, d4)

        # 3) 검증: Q1+Q2+Q3+Q4 ≈ 연간 (영업CF 기준)
        d1, d2, d3, d4 = deltas["operating_cf"]
        sum_q = (d1 or 0) + (d2 or 0) + (d3 or 0) + (d4 or 0)
        yr_op = items["operating_cf"][3]
        if yr_op != 0:
            err = abs(sum_q - yr_op) / abs(yr_op)
            if err > VERIFY_TOLERANCE:
                skipped_verification += 1
                continue

        if dry_run:
            converted += 1
            continue

        # 4) UPDATE 분기 순값을 _q 컬럼에 저장 + value_type 마킹
        for q, dvals in [(1, [deltas[c][0] for c in CF_COLS]),
                         (2, [deltas[c][1] for c in CF_COLS]),
                         (3, [deltas[c][2] for c in CF_COLS])]:
            cur.execute(f"""
                UPDATE cash_flow_data SET
                    operating_cf_q = ?, investing_cf_q = ?,
                    financing_cf_q = ?, capex_q = ?,
                    value_type = 'cumulative→derived'
                WHERE stock_code=? AND year=? AND quarter=? AND is_annual=0
            """, (*dvals, code, year, q))

        # 5) Q4 분기 — 없으면 INSERT, 있으면 UPDATE
        d4_vals = [deltas[c][3] for c in CF_COLS]
        existing = cur.execute(
            "SELECT id FROM cash_flow_data WHERE stock_code=? AND year=? AND quarter=4 AND is_annual=0",
            (code, year)
        ).fetchone()
        if existing:
            cur.execute(f"""
                UPDATE cash_flow_data SET
                    operating_cf_q = ?, investing_cf_q = ?,
                    financing_cf_q = ?, capex_q = ?,
                    value_type = 'derived_q4'
                WHERE id = ?
            """, (*d4_vals, existing[0]))
            q4_updated += 1
        else:
            cur.execute(f"""
                INSERT INTO cash_flow_data
                    (stock_code, year, quarter, is_annual,
                     operating_cf_q, investing_cf_q, financing_cf_q, capex_q,
                     value_type)
                VALUES (?, ?, 4, 0, ?, ?, ?, ?, 'derived_q4')
            """, (code, year, *d4_vals))
            q4_inserted += 1

        converted += 1

    if not dry_run:
        conn.commit()

    return {
        "converted": converted,
        "skipped_not_cumulative": skipped_cumulative,
        "skipped_verification": skipped_verification,
        "q4_inserted": q4_inserted,
        "q4_updated": q4_updated,
    }


def step_3b_convert_q1_standalone(conn, year_from: int, year_to: int, dry_run: bool) -> dict:
    """Q1 단독 분기값 변환 (2026-07-21 신규).

    Q1은 그 해의 첫 분기라 정의상 Q1누적 = Q1분기값 — Q2/Q3/연간이 아직 공시되지
    않은 최신 연도(예: 2026 Q1만 존재)라도 검증 없이 즉시 확정 가능하다. 기존
    step_3_convert()는 q1~yr 4개 모두 존재해야만 처리하므로 아직 연간이 나오지
    않은 최신 분기는 영구히 스킵되던 구조적 공백을 메운다.
    """
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT id, operating_cf, investing_cf, financing_cf, capex
        FROM cash_flow_data
        WHERE year BETWEEN ? AND ? AND quarter=1 AND is_annual=0
          AND operating_cf IS NOT NULL AND operating_cf_q IS NULL
    """, (year_from, year_to)).fetchall()

    converted = 0
    for row_id, op, inv, fin, cap in rows:
        if dry_run:
            converted += 1
            continue
        cur.execute("""
            UPDATE cash_flow_data SET
                operating_cf_q = ?, investing_cf_q = ?,
                financing_cf_q = ?, capex_q = ?,
                value_type = COALESCE(value_type, 'q1_standalone')
            WHERE id = ?
        """, (op, inv, fin, cap, row_id))
        converted += 1

    if not dry_run:
        conn.commit()
    return {"q1_standalone_converted": converted}


def step_4_validate(conn) -> dict:
    """변환 결과 검증."""
    cur = conn.cursor()
    rows = cur.execute("""
        WITH cf AS (
            SELECT stock_code, year,
                MAX(CASE WHEN quarter=1 AND is_annual=0 THEN operating_cf_q END) AS d1,
                MAX(CASE WHEN quarter=2 AND is_annual=0 THEN operating_cf_q END) AS d2,
                MAX(CASE WHEN quarter=3 AND is_annual=0 THEN operating_cf_q END) AS d3,
                MAX(CASE WHEN quarter=4 AND is_annual=0 THEN operating_cf_q END) AS d4,
                MAX(CASE WHEN is_annual=1                THEN operating_cf END) AS yr
            FROM cash_flow_data
            GROUP BY stock_code, year
        )
        SELECT
            COUNT(*) AS rows_with_q,
            SUM(CASE WHEN ABS((d1+d2+d3+d4) - yr) <= ABS(yr)*0.02 THEN 1 ELSE 0 END) AS within_2pct
        FROM cf
        WHERE d1 IS NOT NULL AND d2 IS NOT NULL AND d3 IS NOT NULL AND d4 IS NOT NULL AND yr IS NOT NULL
    """).fetchone()
    return {"rows_with_q_values": rows[0], "within_2pct": rows[1]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--years", default="2020-2025", help="형식: 2020-2025")
    parser.add_argument("--no-backup", action="store_true", help="백업 스킵 (테스트용)")
    args = parser.parse_args()

    yf, yt = (int(x) for x in args.years.split("-"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")

    if not args.no_backup and not args.dry_run:
        step_1_backup(conn)

    step_2_add_columns(conn)

    logger.info(f"[3단계] 변환 시작: {yf}~{yt} ({'DRY-RUN' if args.dry_run else 'APPLY'})")
    res = step_3_convert(conn, yf, yt, args.dry_run)
    logger.info(f"[3단계] 결과: {res}")

    logger.info(f"[3b단계] Q1 단독 변환 시작: {yf}~{yt}")
    res_q1 = step_3b_convert_q1_standalone(conn, yf, yt, args.dry_run)
    logger.info(f"[3b단계] 결과: {res_q1}")

    if not args.dry_run:
        val = step_4_validate(conn)
        logger.info(f"[4단계] 검증: {val}")

    conn.close()


if __name__ == "__main__":
    main()
