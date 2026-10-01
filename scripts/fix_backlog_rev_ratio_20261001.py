"""
scripts/fix_backlog_rev_ratio_20261001.py

order_backlog.backlog_to_rev 전체 재계산
  backlog_to_rev = backlog_amount / financial_data.revenue (CFS 우선, 없으면 OFS)
  현재 3건만 채워져 있는 상태 → 전체 업데이트

run_id: fix_backlog_rev_20261001
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db_compat import connect_primary_db

RUN_ID = "fix_backlog_rev_20261001"
BATCH = 2000


def main(dry_run: bool = False) -> None:
    conn = connect_primary_db(timeout=300)
    conn.execute("SET statement_timeout = '300s'")

    # revenue 로드 (CFS 우선, 없으면 OFS)
    print("financial_data.revenue 로드 중...")
    rev_map: dict[tuple, float] = {}
    for r in conn.execute("""
        SELECT stock_code, year, quarter, report_type, revenue
        FROM financial_data
        WHERE revenue IS NOT NULL AND revenue > 0
        ORDER BY stock_code, year, quarter,
                 CASE report_type WHEN 'CFS' THEN 0 ELSE 1 END
    """).fetchall():
        if r[1] is None or r[2] is None:
            continue
        key = (r[0], int(r[1]), int(r[2]))
        if key not in rev_map:
            rev_map[key] = float(r[4])

    print(f"  revenue 로드: {len(rev_map):,}건")

    # order_backlog 전체 처리
    rows = conn.execute("""
        SELECT id, stock_code, year, quarter, backlog_amount, backlog_to_rev
        FROM order_backlog
        WHERE backlog_amount IS NOT NULL AND backlog_amount > 0
        ORDER BY id
    """).fetchall()

    print(f"처리 대상 (backlog_amount 있음): {len(rows):,}건")

    updated = skipped = 0
    for row in rows:
        rid, sc, yr, qr, amount, old_ratio = row
        if yr is None or qr is None:
            skipped += 1
            continue

        rev = rev_map.get((sc, int(yr), int(qr)))
        if rev is None:
            skipped += 1
            continue

        new_ratio = float(amount) / rev

        if not dry_run:
            conn.execute(
                "UPDATE order_backlog SET backlog_to_rev=%s WHERE id=%s",
                (new_ratio, rid),
            )
        updated += 1

    if not dry_run:
        conn.commit()

    print(f"  업데이트: {updated:,}건 / 스킵(revenue 없음): {skipped:,}건")

    if not dry_run:
        # 분포 확인
        dist = conn.execute("""
            SELECT
              COUNT(*) FILTER (WHERE backlog_to_rev IS NULL AND backlog_amount IS NOT NULL) AS still_null,
              COUNT(*) FILTER (WHERE backlog_to_rev IS NOT NULL) AS filled,
              PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY backlog_to_rev) AS median,
              PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY backlog_to_rev) AS p95
            FROM order_backlog
        """).fetchone()
        print(f"  최종 채워짐: {dist[1]:,}건, 아직 NULL: {dist[0]:,}건")
        print(f"  중앙값={dist[2]:.2f}, P95={dist[3]:.2f}")

        conn.execute("""
            INSERT INTO data_fix_log
              (table_name, scope, row_count, fix_rule,
               old_value_summary, new_value_summary, source, run_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            "order_backlog",
            "backlog_to_rev 전체 재계산",
            updated,
            "backlog_amount / financial_data.revenue (CFS 우선)",
            "3건만 채워진 상태",
            f"업데이트={updated}, 스킵={skipped}",
            "fix_script",
            RUN_ID,
        ))
        conn.commit()
        print(f"data_fix_log 기록 완료 (run_id: {RUN_ID})")

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
