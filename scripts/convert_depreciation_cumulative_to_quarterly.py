"""
convert_depreciation_cumulative_to_quarterly.py — cash_flow_data.depreciation 누적값을
분기 순값(depreciation_q)으로 변환. convert_cf_cumulative_to_quarterly.py와 동일한
원리(Q1=Q1누적, Q2=Q2누적-Q1누적, Q3=Q3누적-Q2누적, Q4=연간-Q3누적)를 감가상각비에 적용.

배경: operating_cf_q 변환 재실행 후에도 2020~2021 커버리지가 낮은 근본 원인은 원본
cash_flow_data 자체가 해당 연도 전체 유니버스의 절반 정도만 수집돼 있기 때문(DART 재수집
필요, 별도 작업). 반면 depreciation은 raw(누적) 값이 이미 존재하는데도 quarterly 변환이
안 된 경우가 있어(2022~2025년 raw 79~89% vs _q 70~73%), 새 DART 호출 없이 기존 데이터에서
즉시 회수 가능한 커버리지 갭이 있음.
"""
from __future__ import annotations
import argparse
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)
DB_PATH = Path(__file__).resolve().parent.parent / "stock.db"

CUMULATIVE_RATIO_MAX = 1.05
VERIFY_TOLERANCE = 0.02


def _is_cumulative(q1, q2, q3, yr) -> bool:
    if None in (q1, q2, q3, yr):
        return False
    if yr == 0:
        return False
    if abs(q3) > abs(yr) * CUMULATIVE_RATIO_MAX:
        return False
    return True


def convert(conn, year_from: int, year_to: int, dry_run: bool) -> dict:
    cur = conn.cursor()
    rows = cur.execute("""
        WITH cf AS (
            SELECT stock_code, year,
                MAX(CASE WHEN quarter=1 AND is_annual=0 THEN depreciation END) AS q1,
                MAX(CASE WHEN quarter=2 AND is_annual=0 THEN depreciation END) AS q2,
                MAX(CASE WHEN quarter=3 AND is_annual=0 THEN depreciation END) AS q3,
                MAX(CASE WHEN is_annual=1                THEN depreciation END) AS yr
            FROM cash_flow_data
            WHERE year BETWEEN ? AND ?
            GROUP BY stock_code, year
        )
        SELECT * FROM cf WHERE q1 IS NOT NULL AND q2 IS NOT NULL AND q3 IS NOT NULL AND yr IS NOT NULL
    """, (year_from, year_to)).fetchall()

    converted = 0
    skipped_cumulative = 0
    skipped_verification = 0
    q4_inserted = 0
    q4_updated = 0
    already_has_q = 0

    for code, year, q1, q2, q3, yr in rows:
        if not _is_cumulative(q1, q2, q3, yr):
            skipped_cumulative += 1
            continue
        d1, d2, d3, d4 = q1, q2 - q1, q3 - q2, yr - q3
        sum_q = d1 + d2 + d3 + d4
        if yr != 0 and abs(sum_q - yr) / abs(yr) > VERIFY_TOLERANCE:
            skipped_verification += 1
            continue

        if dry_run:
            converted += 1
            continue

        for q, dval in [(1, d1), (2, d2), (3, d3)]:
            existing_q = cur.execute(
                "SELECT depreciation_q FROM cash_flow_data WHERE stock_code=? AND year=? AND quarter=? AND is_annual=0",
                (code, year, q)
            ).fetchone()
            if existing_q and existing_q[0] is not None:
                already_has_q += 1
                continue
            cur.execute("""
                UPDATE cash_flow_data SET depreciation_q = ?
                WHERE stock_code=? AND year=? AND quarter=? AND is_annual=0
            """, (dval, code, year, q))

        existing_q4 = cur.execute(
            "SELECT id, depreciation_q FROM cash_flow_data WHERE stock_code=? AND year=? AND quarter=4 AND is_annual=0",
            (code, year)
        ).fetchone()
        if existing_q4:
            if existing_q4[1] is None:
                cur.execute("UPDATE cash_flow_data SET depreciation_q=? WHERE id=?", (d4, existing_q4[0]))
                q4_updated += 1
        else:
            cur.execute("""
                INSERT INTO cash_flow_data (stock_code, year, quarter, is_annual, depreciation_q, value_type)
                VALUES (?, ?, 4, 0, ?, 'derived_q4_dep')
            """, (code, year, d4))
            q4_inserted += 1

        converted += 1

    if not dry_run:
        conn.commit()

    return {
        "converted": converted, "skipped_not_cumulative": skipped_cumulative,
        "skipped_verification": skipped_verification, "q4_inserted": q4_inserted,
        "q4_updated": q4_updated, "already_has_q_skipped": already_has_q,
    }


def convert_q1_standalone(conn, year_from: int, year_to: int, dry_run: bool) -> dict:
    """Q1 단독 변환 (2026-07-21 신규) — Q1누적=Q1분기값이라 Q2/Q3/연간 미공시 상태에서도
    즉시 확정 가능. convert()는 4개 모두 존재해야 처리하므로 최신 분기(예: 2026 Q1만
    존재)가 영구히 스킵되던 공백을 메운다. convert_cf_cumulative_to_quarterly.py의
    step_3b_convert_q1_standalone과 동일 원리."""
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT id, depreciation FROM cash_flow_data
        WHERE year BETWEEN ? AND ? AND quarter=1 AND is_annual=0
          AND depreciation IS NOT NULL AND depreciation_q IS NULL
    """, (year_from, year_to)).fetchall()
    converted = 0
    for row_id, dep in rows:
        if dry_run:
            converted += 1
            continue
        cur.execute("UPDATE cash_flow_data SET depreciation_q=? WHERE id=?", (dep, row_id))
        converted += 1
    if not dry_run:
        conn.commit()
    return {"q1_standalone_converted": converted}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--years", default="2016-2026")
    args = parser.parse_args()
    yf, yt = (int(x) for x in args.years.split("-"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    logger.info(f"변환 시작: {yf}~{yt} ({'DRY-RUN' if args.dry_run else 'APPLY'})")
    res = convert(conn, yf, yt, args.dry_run)
    logger.info(f"결과: {res}")
    res_q1 = convert_q1_standalone(conn, yf, yt, args.dry_run)
    logger.info(f"Q1단독 결과: {res_q1}")
    conn.close()


if __name__ == "__main__":
    main()
