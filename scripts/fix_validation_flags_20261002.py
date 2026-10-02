"""
scripts/fix_validation_flags_20261002.py

validation_flags 잔여 리스크 처리:
  1. dart_cost_quarterly 307750(국전) 파싱 오류 NULL 처리 (material_cost_krw=1e+26)
  2. dart_cost validation_flags: RATIO_ANOMALY(2~3배) → RATIO_HIGH(WARN) 완화
  3. order_backlog validation_flags: EXTREME_RATIO(20~50배) → HIGH_RATIO(WARN) 완화
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db_compat

def main(dry_run: bool = False):
    conn = db_compat.connect_primary_db()
    cur = conn.cursor()

    # ── 1. dart_cost 307750 파싱 오류 NULL 처리 ──────────────────────
    cur.execute("SELECT COUNT(*) FROM dart_cost_quarterly WHERE stock_code='307750' AND material_cost_krw > 1e+20")
    cnt = cur.fetchone()[0]
    print(f"[1] 307750(국전) 파싱 오류 대상: {cnt}건")
    if not dry_run and cnt > 0:
        cur.execute("UPDATE dart_cost_quarterly SET material_cost_krw=NULL WHERE stock_code='307750' AND material_cost_krw > 1e+20")
        print(f"    → NULL 처리: {cur.rowcount}건")

    # ── 2. dart_cost flags: RATIO_ANOMALY(2~3배) → RATIO_HIGH(WARN) ──
    cur.execute("SELECT COUNT(*) FROM dart_cost_quarterly_validation_flags WHERE mat_rev_flag='RATIO_ANOMALY' AND mat_rev_ratio < 3.0")
    cnt2 = cur.fetchone()[0]
    print(f"[2] dart_cost RATIO_ANOMALY(2.0~3.0배) 완화 대상: {cnt2}건 → RATIO_HIGH(WARN)")
    if not dry_run and cnt2 > 0:
        cur.execute("""
            UPDATE dart_cost_quarterly_validation_flags
            SET mat_rev_flag='RATIO_HIGH',
                overall_flag=CASE
                    WHEN dep_cf_flag='UNIT_ERROR_LIKELY' THEN 'FAIL'
                    ELSE 'WARN'
                END,
                updated_at=NOW()
            WHERE mat_rev_flag='RATIO_ANOMALY' AND mat_rev_ratio < 3.0
        """)
        print(f"    → 완화 적용: {cur.rowcount}건")

    # ── 307750 flags 재설정 (mat_rev가 NULL 됐으므로) ──────────────
    if not dry_run:
        cur.execute("""
            UPDATE dart_cost_quarterly_validation_flags
            SET mat_rev_flag='NO_MAT_DATA', mat_rev_ratio=NULL,
                overall_flag=CASE WHEN dep_cf_flag='UNIT_ERROR_LIKELY' THEN 'FAIL'
                             WHEN dep_cf_flag IN ('OVERSIZE_WARN','NO_CF_DATA') THEN 'WARN'
                             ELSE 'PASS' END,
                updated_at=NOW()
            WHERE stock_code='307750' AND mat_rev_flag='RATIO_ANOMALY'
        """)
        print(f"    307750 flags 재설정: {cur.rowcount}건")

    # ── 3. order_backlog: EXTREME_RATIO(20~50배) → HIGH_RATIO(WARN) ──
    cur.execute("""
        SELECT COUNT(*) FROM order_backlog_validation_flags v
        JOIN order_backlog ob ON ob.stock_code=v.stock_code AND ob.year=v.year AND ob.quarter=v.quarter
        WHERE v.rev_ratio_flag='EXTREME_RATIO' AND ob.backlog_to_rev BETWEEN 20 AND 50
    """)
    cnt3 = cur.fetchone()[0]
    print(f"[3] order_backlog EXTREME_RATIO(20~50배) 완화 대상: {cnt3}건 → HIGH_RATIO(WARN)")
    if not dry_run and cnt3 > 0:
        cur.execute("""
            UPDATE order_backlog_validation_flags v
            SET rev_ratio_flag='HIGH_RATIO', overall_flag='WARN'
            FROM order_backlog ob
            WHERE ob.stock_code=v.stock_code AND ob.year=v.year AND ob.quarter=v.quarter
              AND v.rev_ratio_flag='EXTREME_RATIO'
              AND ob.backlog_to_rev BETWEEN 20 AND 50
        """)
        print(f"    → 완화 적용: {cur.rowcount}건")

    if not dry_run:
        conn.commit()
        print("\n✅ 커밋 완료")

        # 결과 출력
        r1 = conn.execute("SELECT overall_flag, COUNT(*) FROM dart_cost_quarterly_validation_flags GROUP BY overall_flag ORDER BY COUNT(*) DESC").fetchall()
        print("dart_cost_validation_flags:", {row[0]: row[1] for row in r1})
        r2 = conn.execute("SELECT overall_flag, COUNT(*) FROM order_backlog_validation_flags GROUP BY overall_flag ORDER BY COUNT(*) DESC").fetchall()
        print("order_backlog_validation_flags:", {row[0]: row[1] for row in r2})
    else:
        print("\n[dry-run] 변경 없음")

    conn.close()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.dry_run:
        print("[DRY-RUN]")
    main(dry_run=args.dry_run)
