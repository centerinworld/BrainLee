"""
recalculate_q4.py — Q4 분기 데이터 강제 재계산

문제:
  일부 소스(FnGuide, DART 분기)에서 Q4 컬럼이 누적값(= 연간값)으로 잘못 저장됨.
  올바른 Q4 증분 = Annual - Q1 - Q2 - Q3

대상:
  financial_data  : revenue, operating_profit, net_income
  cash_flow_data  : operating_cf, investing_cf, financing_cf, capex

실행:
  # dry-run (변경사항 미리 보기)
  python3 scripts/recalculate_q4.py --dry-run

  # 실제 적용
  python3 scripts/recalculate_q4.py

  # 특정 연도/종목
  python3 scripts/recalculate_q4.py --year 2024 --codes 005930 000660
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)
DB_PATH = Path(__file__).resolve().parent.parent / "stock.db"

_FIN_FIELDS = ["revenue", "operating_profit", "net_income"]
_CF_FIELDS  = ["operating_cf", "investing_cf", "financing_cf", "capex"]


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=120)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=120000")
    return c


def _get_val(row, field: str):
    try:
        v = row[field]
        return float(v) if v is not None else None
    except Exception:
        return None


def recalculate_q4_financial(
    conn: sqlite3.Connection,
    year: int,
    report_type: str,
    stock_code: str,
    dry_run: bool,
) -> dict:
    """financial_data Q4 재계산."""
    result = {"fixed": 0, "skipped": 0, "already_correct": 0}

    # Annual + Q1~Q3 모두 있는 종목만 처리
    rows = conn.execute("""
        SELECT quarter, revenue, operating_profit, net_income
        FROM financial_data
        WHERE stock_code=? AND year=? AND is_annual IN (0,1) AND report_type=?
          AND quarter IN (0,1,2,3,4)
        ORDER BY quarter
    """, (stock_code, year, report_type)).fetchall()

    by_q = {r["quarter"]: r for r in rows}
    annual = by_q.get(0)
    q1, q2, q3, q4_row = by_q.get(1), by_q.get(2), by_q.get(3), by_q.get(4)

    if not (annual and q1 and q2 and q3):
        result["skipped"] += 1
        return result

    updates: dict[str, float] = {}
    for f in _FIN_FIELDS:
        av = _get_val(annual, f)
        v1 = _get_val(q1, f)
        v2 = _get_val(q2, f)
        v3 = _get_val(q3, f)
        if None in (av, v1, v2, v3):
            continue

        q4_correct = av - v1 - v2 - v3

        if q4_row:
            current_q4 = _get_val(q4_row, f)
            if current_q4 is None:
                updates[f] = q4_correct
            else:
                # 현재 Q4가 Annual과 거의 같으면 누적값 오류
                if av != 0 and abs(current_q4 - av) / abs(av) < 0.01:
                    updates[f] = q4_correct
                    logger.info(
                        f"{stock_code} {year} Q4 {f}: "
                        f"누적값({current_q4/1e8:.1f}억) → 증분({q4_correct/1e8:.1f}억) 수정"
                    )
        else:
            updates[f] = q4_correct

    if not updates:
        result["already_correct"] += 1
        return result

    if dry_run:
        logger.info(f"[DRY-RUN] {stock_code} {year} {report_type} Q4 수정 예정: {list(updates.keys())}")
        result["fixed"] += 1
        return result

    if q4_row:
        sets = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [stock_code, year, report_type]
        conn.execute(
            f"UPDATE financial_data SET {sets} "
            f"WHERE stock_code=? AND year=? AND report_type=? AND quarter=4 AND is_annual=0",
            vals
        )
    else:
        # INSERT Q4 행
        fields_str = ", ".join(updates.keys())
        placeholders = ", ".join("?" * len(updates))
        conn.execute(
            f"INSERT INTO financial_data (stock_code, year, quarter, is_annual, report_type, {fields_str}) "
            f"VALUES (?,?,4,0,?,{placeholders})",
            [stock_code, year, report_type] + list(updates.values())
        )
    result["fixed"] += 1
    return result


def recalculate_q4_cashflow(
    conn: sqlite3.Connection,
    year: int,
    report_type: str,
    stock_code: str,
    dry_run: bool,
) -> dict:
    """cash_flow_data Q4 재계산."""
    result = {"fixed": 0, "skipped": 0, "already_correct": 0}

    rows = conn.execute("""
        SELECT quarter, operating_cf, investing_cf, financing_cf, capex
        FROM cash_flow_data
        WHERE stock_code=? AND year=? AND is_annual IN (0,1) AND report_type=?
          AND quarter IN (0,1,2,3,4)
        ORDER BY quarter
    """, (stock_code, year, report_type)).fetchall()

    by_q = {r["quarter"]: r for r in rows}
    annual = by_q.get(0)
    q1, q2, q3, q4_row = by_q.get(1), by_q.get(2), by_q.get(3), by_q.get(4)

    if not (annual and q1 and q2 and q3):
        result["skipped"] += 1
        return result

    updates: dict[str, float] = {}
    for f in _CF_FIELDS:
        av = _get_val(annual, f)
        v1 = _get_val(q1, f)
        v2 = _get_val(q2, f)
        v3 = _get_val(q3, f)
        if None in (av, v1, v2, v3):
            continue

        q4_correct = av - v1 - v2 - v3

        if q4_row:
            current_q4 = _get_val(q4_row, f)
            if current_q4 is None:
                updates[f] = q4_correct
            elif av != 0 and abs(current_q4 - av) / abs(av) < 0.01:
                updates[f] = q4_correct
                logger.info(
                    f"{stock_code} {year} Q4 {f}(CF): "
                    f"누적값({current_q4/1e8:.1f}억) → 증분({q4_correct/1e8:.1f}억) 수정"
                )
        else:
            updates[f] = q4_correct

    if not updates:
        result["already_correct"] += 1
        return result

    if dry_run:
        logger.info(f"[DRY-RUN] {stock_code} {year} {report_type} Q4 CF 수정 예정: {list(updates.keys())}")
        result["fixed"] += 1
        return result

    if q4_row:
        sets = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [stock_code, year, report_type]
        conn.execute(
            f"UPDATE cash_flow_data SET {sets} "
            f"WHERE stock_code=? AND year=? AND report_type=? AND quarter=4 AND is_annual=0",
            vals
        )
    else:
        fields_str = ", ".join(updates.keys())
        placeholders = ", ".join("?" * len(updates))
        conn.execute(
            f"INSERT INTO cash_flow_data (stock_code, year, quarter, is_annual, report_type, {fields_str}) "
            f"VALUES (?,?,4,0,?,{placeholders})",
            [stock_code, year, report_type] + list(updates.values())
        )
    result["fixed"] += 1
    return result


def run(
    dry_run: bool,
    years: list[int],
    codes: list[str],
    report_types: list[str],
) -> dict:
    conn = _conn()

    # 처리할 종목 목록
    if codes:
        all_codes = codes
    else:
        all_codes = [
            r[0] for r in conn.execute("""
                SELECT DISTINCT stock_code FROM financial_data
                WHERE quarter IN (1,2,3) AND is_annual=0
                  AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
            """).fetchall()
        ]

    total = {"fixed": 0, "skipped": 0, "already_correct": 0, "fin_fixed": 0, "cf_fixed": 0}
    committed = 0

    for i, code in enumerate(all_codes, 1):
        for yr in years:
            for rt in report_types:
                fin_r = recalculate_q4_financial(conn, yr, rt, code, dry_run)
                cf_r  = recalculate_q4_cashflow(conn, yr, rt, code, dry_run)
                total["fixed"]         += fin_r["fixed"] + cf_r["fixed"]
                total["skipped"]       += fin_r["skipped"] + cf_r["skipped"]
                total["already_correct"] += fin_r["already_correct"] + cf_r["already_correct"]
                total["fin_fixed"]     += fin_r["fixed"]
                total["cf_fixed"]      += cf_r["fixed"]

        if not dry_run and i % 200 == 0:
            conn.commit()
            committed = i
            logger.info(f"[{i}/{len(all_codes)}] fixed={total['fixed']}")

    if not dry_run and committed < len(all_codes):
        conn.commit()

    conn.close()
    return total


def main():
    ap = argparse.ArgumentParser(description="Q4 분기 데이터 재계산 (Annual - Q1 - Q2 - Q3)")
    ap.add_argument("--dry-run",      action="store_true", help="변경하지 않고 결과만 출력")
    ap.add_argument("--years",        nargs="+", type=int, default=list(range(2015, 2026)))
    ap.add_argument("--codes",        nargs="+", default=[])
    ap.add_argument("--report-types", nargs="+", default=["CFS", "OFS"])
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    result = run(
        dry_run=args.dry_run,
        years=args.years,
        codes=args.codes,
        report_types=args.report_types,
    )

    mode = "[DRY-RUN] " if args.dry_run else ""
    print(f"\n{mode}Q4 재계산 결과:")
    print(f"  수정됨:  financial_data={result['fin_fixed']}건, cash_flow_data={result['cf_fixed']}건")
    print(f"  이미정확: {result['already_correct']}건")
    print(f"  건너뜀:  {result['skipped']}건 (Q1~Q3 미수집)")
    print(f"  합계 수정: {result['fixed']}건")


if __name__ == "__main__":
    main()
