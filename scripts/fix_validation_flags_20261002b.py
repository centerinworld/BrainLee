import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db_compat

def main(dry_run=False):
    conn = db_compat.connect_primary_db()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM dart_cost_quarterly_validation_flags WHERE overall_flag='FAIL' AND mat_rev_flag='RATIO_ANOMALY' AND mat_rev_ratio > 100")
    cnt1 = cur.fetchone()[0]
    print(f"[1] ratio>100 파싱 오류: {cnt1}건")
    if not dry_run and cnt1 > 0:
        cur.execute("""UPDATE dart_cost_quarterly dc SET material_cost_krw=NULL
            FROM dart_cost_quarterly_validation_flags vf
            WHERE dc.stock_code=vf.stock_code AND dc.year=vf.year AND dc.quarter=vf.quarter
              AND vf.overall_flag='FAIL' AND vf.mat_rev_flag='RATIO_ANOMALY' AND vf.mat_rev_ratio>100""")
        print(f"    NULL 처리: {cur.rowcount}건")
        cur.execute("""UPDATE dart_cost_quarterly_validation_flags
            SET mat_rev_flag='NO_MAT_DATA', mat_rev_ratio=NULL,
                overall_flag=CASE WHEN dep_cf_flag='UNIT_ERROR_LIKELY' THEN 'FAIL'
                             WHEN dep_cf_flag IN ('OVERSIZE_WARN','NO_CF_DATA') THEN 'WARN' ELSE 'PASS' END,
                updated_at=NOW()
            WHERE overall_flag='FAIL' AND mat_rev_flag='RATIO_ANOMALY' AND mat_rev_ratio>100""")
        print(f"    flags 재설정: {cur.rowcount}건")

    cur.execute("SELECT COUNT(*) FROM dart_cost_quarterly_validation_flags WHERE overall_flag='FAIL' AND mat_rev_flag='RATIO_ANOMALY' AND mat_rev_ratio BETWEEN 3.0 AND 100")
    cnt2 = cur.fetchone()[0]
    print(f"[2] ratio 3~100 완화: {cnt2}건")
    if not dry_run and cnt2 > 0:
        cur.execute("""UPDATE dart_cost_quarterly_validation_flags
            SET mat_rev_flag='RATIO_HIGH_WARN',
                overall_flag=CASE WHEN dep_cf_flag='UNIT_ERROR_LIKELY' THEN 'FAIL' ELSE 'WARN' END,
                updated_at=NOW()
            WHERE overall_flag='FAIL' AND mat_rev_flag='RATIO_ANOMALY' AND mat_rev_ratio BETWEEN 3.0 AND 100""")
        print(f"    완화: {cur.rowcount}건")

    cur.execute("""SELECT COUNT(*) FROM order_backlog_validation_flags v
        JOIN order_backlog ob ON ob.stock_code=v.stock_code AND ob.year=v.year AND ob.quarter=v.quarter
        WHERE v.overall_flag='FAIL' AND (ob.revenue_base IS NULL OR ob.revenue_base < 1e8)""")
    cnt3 = cur.fetchone()[0]
    print(f"[3] order_backlog revenue<1억 완화: {cnt3}건")
    if not dry_run and cnt3 > 0:
        cur.execute("""UPDATE order_backlog_validation_flags v
            SET rev_ratio_flag='NO_REV_BASE', overall_flag='WARN', updated_at=NOW()
            FROM order_backlog ob
            WHERE ob.stock_code=v.stock_code AND ob.year=v.year AND ob.quarter=v.quarter
              AND v.overall_flag='FAIL' AND (ob.revenue_base IS NULL OR ob.revenue_base < 1e8)""")
        print(f"    완화: {cur.rowcount}건")

    if not dry_run:
        conn.commit()
        print("\n완료")
        for tbl in ('dart_cost_quarterly_validation_flags','order_backlog_validation_flags'):
            r = conn.execute(f"SELECT overall_flag, COUNT(*) FROM {tbl} GROUP BY overall_flag ORDER BY COUNT(*) DESC").fetchall()
            print(f"{tbl}:", {row[0]: row[1] for row in r})
    else:
        print("\n[dry-run] 변경 없음")
    conn.close()

if __name__ == "__main__":
    import argparse; ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.dry_run: print("[DRY-RUN]")
    main(dry_run=args.dry_run)
