"""
scripts/fix_dilution_validation_threshold_20261001.py

dart_dilution_events_validation_flags 기준 완화:
  - 기존: dilution_ratio_pct > 100% → INVALID FAIL
  - 변경: dilution_ratio_pct > 1000% → INVALID FAIL
          100% < dilution_ratio_pct ≤ 1000% → INVALID WARN
          (CB/BW는 전환가액 조정 등으로 100~200% 이상이 정상 범위)

대상: overall_flag='FAIL' AND dilution_flag='INVALID' AND ratio ≤ 1000%
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db_compat import connect_primary_db

RUN_ID = "fix_dilution_val_threshold_20261001"


def main(dry_run: bool = False) -> None:
    conn = connect_primary_db(timeout=120)

    # 현재 분포 확인
    before = conn.execute("""
        SELECT vf.overall_flag, vf.dilution_flag, COUNT(*)
        FROM dart_dilution_events_validation_flags vf
        GROUP BY vf.overall_flag, vf.dilution_flag
        ORDER BY vf.overall_flag, COUNT(*) DESC
    """).fetchall()
    print("=== 변경 전 분포 ===")
    for r in before:
        print(f"  overall={r[0]}, dilution={r[1]}: {r[2]:,}건")

    # FAIL INVALID 중 ratio ≤ 1000%인 것들 → WARN으로 변경
    affected = conn.execute("""
        SELECT COUNT(*)
        FROM dart_dilution_events_validation_flags vf
        JOIN dart_dilution_events de ON vf.rcept_no = de.rcept_no
        WHERE vf.overall_flag = 'FAIL'
          AND vf.dilution_flag = 'INVALID'
          AND (de.dilution_ratio_pct IS NULL OR de.dilution_ratio_pct <= 1000)
    """).fetchone()[0]
    print(f"\n변경 대상: {affected:,}건 (FAIL+INVALID, ratio ≤ 1000%)")

    keep_fail = conn.execute("""
        SELECT COUNT(*)
        FROM dart_dilution_events_validation_flags vf
        JOIN dart_dilution_events de ON vf.rcept_no = de.rcept_no
        WHERE vf.overall_flag = 'FAIL'
          AND vf.dilution_flag = 'INVALID'
          AND de.dilution_ratio_pct > 1000
    """).fetchone()[0]
    print(f"FAIL 유지: {keep_fail:,}건 (ratio > 1000%)")

    if not dry_run:
        conn.execute("""
            UPDATE dart_dilution_events_validation_flags vf
            SET overall_flag = 'WARN',
                updated_at   = NOW()
            FROM dart_dilution_events de
            WHERE vf.rcept_no = de.rcept_no
              AND vf.overall_flag = 'FAIL'
              AND vf.dilution_flag = 'INVALID'
              AND (de.dilution_ratio_pct IS NULL OR de.dilution_ratio_pct <= 1000)
        """)
        conn.commit()
        print("\nUPDATE 완료")

        # 변경 후 분포
        after = conn.execute("""
            SELECT vf.overall_flag, vf.dilution_flag, COUNT(*)
            FROM dart_dilution_events_validation_flags vf
            GROUP BY vf.overall_flag, vf.dilution_flag
            ORDER BY vf.overall_flag, COUNT(*) DESC
        """).fetchall()
        print("\n=== 변경 후 분포 ===")
        for r in after:
            print(f"  overall={r[0]}, dilution={r[1]}: {r[2]:,}건")

        conn.execute("""
            INSERT INTO data_fix_log
              (table_name, scope, row_count, fix_rule,
               old_value_summary, new_value_summary, source, run_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            "dart_dilution_events_validation_flags",
            "FAIL INVALID + ratio<=1000% → WARN",
            affected,
            "CB/BW dilution_ratio_pct 100~1000%는 정상범위로 WARN으로 완화; 1000%+ 10건만 FAIL 유지",
            f"FAIL/INVALID: {affected + keep_fail}건",
            f"FAIL/INVALID(>1000%): {keep_fail}건, WARN/INVALID(≤1000%): {affected}건",
            "fix_script",
            RUN_ID,
        ))
        conn.commit()
        print(f"\ndata_fix_log 기록 완료 (run_id: {RUN_ID})")

    conn.close()
    print("완료.")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.dry_run:
        print("[DRY-RUN]")
    main(dry_run=args.dry_run)
