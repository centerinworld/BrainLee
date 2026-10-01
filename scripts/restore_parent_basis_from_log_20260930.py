"""
restore_parent_basis_from_log_20260930.py

2016~2020 연간 net_income / total_equity 지배주주 기준 복원
- 34차 dart_parent_basis_annual_20260926_132915 에서 저장된 지배주주 기준값을
  36차 dart_multi_truth_20260926_155241 가 전체 기준으로 덮어쓴 것을 복구
- DART API 재호출 없이 financial_fix_log에서 직접 복원
- revenue / operating_profit 은 아티팩트 가능성으로 제외

run_id: restore_parent_basis_20260930
"""
from __future__ import annotations
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db_compat import connect_primary_db

RUN_ID     = "restore_parent_basis_20260930"
PARENT_RUN = "dart_parent_basis_annual_20260926_132915"
MULTI_RUN  = "dart_multi_truth_20260926_155241"
FIELDS     = ["net_income", "total_equity"]   # revenue/op_profit 제외


def main():
    conn = connect_primary_db(timeout=120)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"=== 지배주주 기준 복원 시작 ({now_str}) ===\n")

    restored = skipped = already_ok = 0
    ts = datetime.now(timezone.utc).isoformat()

    for field in FIELDS:
        # parent_basis log에 있는 값 목록
        candidates = conn.execute(f"""
            SELECT pb.stock_code, pb.year, pb.is_annual, pb.report_type,
                   CAST(pb.new_value AS FLOAT) as parent_val,
                   CAST(mt.new_value AS FLOAT) as multi_val
            FROM financial_fix_log pb
            JOIN financial_fix_log mt
                ON mt.stock_code=pb.stock_code AND mt.year=pb.year
                AND mt.field_name=pb.field_name AND mt.is_annual=pb.is_annual
            WHERE pb.run_id=%s AND mt.run_id=%s
              AND pb.field_name=%s
              AND pb.new_value IS NOT NULL AND mt.new_value IS NOT NULL
              AND CAST(pb.new_value AS FLOAT) != CAST(mt.new_value AS FLOAT)
        """, (PARENT_RUN, MULTI_RUN, field)).fetchall()

        print(f"[{field}] 후보: {len(candidates)}건")
        f_restored = f_skipped = f_already_ok = 0

        for row in candidates:
            code, year, is_annual, rtype, parent_val, multi_val = (
                row[0], row[1], row[2], row[3], row[4], row[5]
            )

            # 현재 DB 값 조회
            is_annual_bool = bool(int(is_annual)) if is_annual is not None else False
            fd = conn.execute(f"""
                SELECT id, {field} FROM financial_data
                WHERE stock_code=%s AND year=%s AND is_annual=%s
                  AND report_type=%s
                LIMIT 1
            """, (code, year, is_annual_bool, rtype)).fetchone()

            if fd is None:
                f_skipped += 1
                continue

            fd_id, cur_val = fd[0], fd[1]
            if cur_val is None:
                f_skipped += 1
                continue

            cur_float = float(cur_val)

            # 현재값이 이미 parent_basis값이면 스킵
            if abs(cur_float - parent_val) < 1.0:
                f_already_ok += 1
                continue

            # 현재값이 multi_truth값이 아닌 다른 값이면 스킵 (다른 작업이 이미 바꿈)
            if abs(cur_float - multi_val) > abs(cur_float) * 0.01 + 1000:
                f_skipped += 1
                continue

            # 복원
            conn.execute(
                f"UPDATE financial_data SET {field}=%s, data_source=data_source||'+parent_restored' WHERE id=%s",
                (parent_val, fd_id)
            )

            # fix_log 기록
            conn.execute("""
                INSERT INTO financial_fix_log
                (stock_code, year, quarter, is_annual, report_type, field_name,
                 old_value, new_value, fix_rule, source, run_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (code, year, 0, is_annual, rtype, field,
                  str(multi_val), str(parent_val),
                  'dart_multi_truth 덮어쓰기 복원 → dart_parent_basis 기준',
                  'financial_fix_log', RUN_ID))

            f_restored += 1
            if f_restored % 100 == 0:
                conn.commit()

        conn.commit()
        print(f"  복원={f_restored}, 이미정상={f_already_ok}, 스킵={f_skipped}")
        restored += f_restored
        skipped += f_skipped
        already_ok += f_already_ok

    # data_fix_log
    conn.execute("""
        INSERT INTO data_fix_log
        (table_name, scope, row_count, fix_rule, old_value_summary, new_value_summary, source, run_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
    """, (
        'financial_data',
        '2016~2020 annual net_income/total_equity 지배주주 기준 복원',
        restored,
        'dart_multi_truth(전체기준) → dart_parent_basis(지배주주기준) 롤백',
        f'dart_multi_truth 덮어쓴 값 {restored+skipped+already_ok}건 대상',
        f'복원={restored}, 이미정상={already_ok}, 스킵={skipped}',
        'financial_fix_log_recovery',
        RUN_ID
    ))
    conn.commit()

    print(f"\n=== 완료: 복원 {restored}건, 이미정상 {already_ok}건, 스킵 {skipped}건 ===")
    print(f"run_id={RUN_ID}")
    conn.close()


if __name__ == "__main__":
    main()
