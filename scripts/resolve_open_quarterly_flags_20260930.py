"""
resolve_open_quarterly_flags_20260930.py
fin_quarterly_validation_flags OPEN 4,156건 정리

처리 전략:
  1. no_data / no_data_bs → STRUCTURAL (데이터 없음)
  2. single_source* / single_cfs* → 현재 financial_data 값과 재비교
     - 참조값(dart_value OR fnguide_value)과 현재 DB값 ratio ≤3% → CONFIRMED
     - ratio ≤15%                                               → CLOSE_MATCH
     - ratio >15% 또는 참조값 없음                              → STRUCTURAL

run_id: resolve_open_fq_20260930
"""
from __future__ import annotations
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db_compat import connect_primary_db

RUN_ID = "resolve_open_fq_20260930"
TOL_CONFIRMED   = 0.03
TOL_CLOSE_MATCH = 0.15
ABS_TOL         = 5_000_000  # 500만원

FIELD_COL = {
    'revenue':          'revenue',
    'operating_profit': 'operating_profit',
    'net_income':       'net_income',
    'total_assets':     'total_assets',
    'total_equity':     'total_equity',
}

def _ratio(a, b):
    if a is None or b is None:
        return None
    if a == b == 0:
        return 0.0
    denom = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / denom

def main():
    conn = connect_primary_db(timeout=120)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"=== fin_quarterly_validation_flags OPEN 정리 시작 ({now}) ===\n")

    rows = conn.execute("""
        SELECT id, stock_code, year, quarter, field, notes, dart_value, fnguide_value
        FROM fin_quarterly_validation_flags
        WHERE status='OPEN'
    """).fetchall()
    print(f"처리 대상: {len(rows)}건")

    structural = confirmed = close_match = ambiguous = 0
    ts = datetime.now(timezone.utc).isoformat()

    for row in rows:
        flag_id    = row[0]
        code       = row[1]
        year       = row[2]
        quarter    = row[3]
        field      = row[4]
        notes      = row[5] or ''
        dart_val   = row[6]
        fg_val     = row[7]

        # 1. no_data 계열 → STRUCTURAL
        if notes.startswith('no_data'):
            conn.execute(
                "UPDATE fin_quarterly_validation_flags SET status='STRUCTURAL', notes=%s WHERE id=%s",
                (notes + '|resolved_structural', flag_id)
            )
            structural += 1
            continue

        # 2. single_source 계열 → 현재 DB값과 재비교
        db_col = FIELD_COL.get(field)
        if db_col is None:
            conn.execute(
                "UPDATE fin_quarterly_validation_flags SET status='STRUCTURAL', notes=%s WHERE id=%s",
                (notes + '|unknown_field', flag_id)
            )
            structural += 1
            continue

        fd_row = conn.execute(
            f"SELECT {db_col} FROM financial_data WHERE stock_code=%s AND year=%s AND quarter=%s AND is_annual=false LIMIT 1",
            (code, year, quarter)
        ).fetchone()
        db_val = fd_row[0] if fd_row else None

        if db_val is None:
            conn.execute(
                "UPDATE fin_quarterly_validation_flags SET status='STRUCTURAL', notes=%s WHERE id=%s",
                (notes + '|no_db_val', flag_id)
            )
            structural += 1
            continue

        # 참조값과 비교
        best_ratio = None
        best_ref   = None
        for ref_val in [dart_val, fg_val]:
            if ref_val is None:
                continue
            r = _ratio(float(db_val), float(ref_val))
            if r is None:
                continue
            if best_ratio is None or r < best_ratio:
                best_ratio = r
                best_ref   = float(ref_val)

        if best_ratio is None:
            # 참조값 전혀 없음 → STRUCTURAL
            conn.execute(
                "UPDATE fin_quarterly_validation_flags SET status='STRUCTURAL', notes=%s WHERE id=%s",
                (notes + '|no_ref_val', flag_id)
            )
            structural += 1
            continue

        abs_diff = abs(float(db_val) - best_ref)
        if best_ratio <= TOL_CONFIRMED or abs_diff <= ABS_TOL:
            new_status = 'CONFIRMED'
            confirmed += 1
        elif best_ratio <= TOL_CLOSE_MATCH:
            new_status = 'CLOSE_MATCH'
            close_match += 1
        else:
            new_status = 'AMBIGUOUS'
            ambiguous += 1

        conn.execute(
            "UPDATE fin_quarterly_validation_flags SET status=%s, ratio=%s, notes=%s WHERE id=%s",
            (new_status, best_ratio, notes + f'|recheck_ratio={best_ratio:.4f}', flag_id)
        )

        if (confirmed + close_match + ambiguous + structural) % 500 == 0:
            conn.commit()

    conn.commit()

    print(f"\n처리 결과:")
    print(f"  STRUCTURAL  : {structural}건")
    print(f"  CONFIRMED   : {confirmed}건")
    print(f"  CLOSE_MATCH : {close_match}건")
    print(f"  AMBIGUOUS   : {ambiguous}건")
    print(f"  합계        : {structural+confirmed+close_match+ambiguous}건")

    # 완료 후 전체 현황
    print("\n완료 후 전체 상태:")
    for row in conn.execute(
        "SELECT status, COUNT(*) FROM fin_quarterly_validation_flags GROUP BY status ORDER BY COUNT(*) DESC"
    ).fetchall():
        print(f"  {row[0]}: {row[1]}")

    # data_fix_log
    conn.execute("""
        INSERT INTO data_fix_log
        (table_name, scope, row_count, fix_rule, old_value_summary, new_value_summary, source, run_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        'fin_quarterly_validation_flags',
        'OPEN 4156건 재분류',
        structural + confirmed + close_match + ambiguous,
        'no_data→STRUCTURAL; single_source→현재DB재비교(CONFIRMED/CLOSE_MATCH/AMBIGUOUS/STRUCTURAL)',
        'status=OPEN, ratio=NULL, 4156건',
        f'STRUCTURAL={structural}, CONFIRMED={confirmed}, CLOSE_MATCH={close_match}, AMBIGUOUS={ambiguous}',
        'internal_recheck',
        RUN_ID
    ))
    conn.commit()
    conn.close()
    print(f"\n=== 완료 (run_id={RUN_ID}) ===")

if __name__ == "__main__":
    main()
