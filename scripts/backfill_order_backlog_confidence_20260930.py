"""
scripts/backfill_order_backlog_confidence_20260930.py

dart_backlog_quarterly 기존 행 중 신뢰도가 과소 책정된 것을 수정하고
order_backlog를 DART API 재호출 없이 업데이트한다.

배경:
  - 베이스 패턴(키워드+숫자+단위명): 신뢰도 0.85로 저장됐으나 단위가 명시적이므로 0.96이 맞음
  - 패턴 1-c(기말 행 마지막 숫자): 0.92 → 0.95
  - 패턴 1-b 폴백(증감표 기말): 0.90 → 0.95
  MIN_OPERATIONAL_CONFIDENCE=0.95 임계값에 걸려 order_backlog가 NULL인 행 ~4,379건 복구.

run_id: backfill_backlog_conf_20260930
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db_compat import connect_primary_db

# 파서 수정 후의 새 신뢰도 매핑 (old → new)
CONF_REMAP = {
    0.85: 0.96,  # 베이스 패턴: 명시 단위 → 0.96
    0.92: 0.95,  # 1-c 패턴: 기말 행 → 0.95
    0.90: 0.95,  # 1-b 폴백: 증감표 기말 → 0.95
}
MIN_OPERATIONAL_CONFIDENCE = 0.95
RUN_ID = "backfill_backlog_conf_20260930"


def _ratio(a: float, b: float) -> float:
    if a <= 0 or b <= 0:
        return 999.0
    return max(a, b) / min(a, b)


def main(dry_run: bool = False):
    conn = connect_primary_db(timeout=60)
    conn.execute("SET statement_timeout = '300s'")

    # 1) dart_backlog_quarterly 신뢰도 업데이트
    total_updated_conf = 0
    for old_conf, new_conf in CONF_REMAP.items():
        rows = conn.execute(
            """
            SELECT stock_code, fiscal_year, fiscal_quarter, report_type,
                   backlog_amount_krw, backlog_confidence
            FROM dart_backlog_quarterly
            WHERE backlog_confidence = %s
              AND backlog_amount_krw IS NOT NULL
              AND backlog_amount_krw > 0
            """,
            (old_conf,),
        ).fetchall()
        if not rows:
            continue
        print(f"  신뢰도 {old_conf} → {new_conf}: {len(rows)}건")
        if not dry_run:
            conn.execute(
                """
                UPDATE dart_backlog_quarterly
                SET backlog_confidence = %s
                WHERE backlog_confidence = %s
                  AND backlog_amount_krw IS NOT NULL
                  AND backlog_amount_krw > 0
                """,
                (new_conf, old_conf),
            )
        total_updated_conf += len(rows)

    if not dry_run:
        conn.commit()
    print(f"\ndart_backlog_quarterly 신뢰도 업데이트: {total_updated_conf}건")

    # 2) 영향받는 종목 목록 추출
    affected_stocks = conn.execute(
        """
        SELECT DISTINCT stock_code, report_type
        FROM dart_backlog_quarterly
        WHERE backlog_confidence >= %s
          AND backlog_amount_krw IS NOT NULL
          AND backlog_amount_krw > 0
        """,
        (MIN_OPERATIONAL_CONFIDENCE,),
    ).fetchall()
    print(f"영향 종목-report_type: {len(affected_stocks)}건")

    # 3) 각 종목에 대해 _refresh_order_backlog_projection 로직 수행
    #    (임계값 MIN_OPERATIONAL_CONFIDENCE 기준으로 재판정)
    accepted_total = 0
    rejected_total = 0
    restored_total = 0

    for stock_code, report_type in affected_stocks:
        rows = conn.execute(
            """
            SELECT fiscal_year, fiscal_quarter, backlog_amount_krw, backlog_confidence
            FROM dart_backlog_quarterly
            WHERE stock_code = %s AND report_type = %s
              AND backlog_amount_krw IS NOT NULL
            ORDER BY fiscal_year, fiscal_quarter
            """,
            (stock_code, report_type),
        ).fetchall()
        if not rows:
            continue

        values = {
            (int(r[0]), int(r[1])): (float(r[2]), float(r[3] or 0))
            for r in rows
        }

        # 연속 기간 20배 초과 체크
        rejected: set[tuple] = set()
        for period, (amount, confidence) in values.items():
            if confidence < MIN_OPERATIONAL_CONFIDENCE or amount <= 0:
                rejected.add(period)

        periods = sorted(values)
        for previous, current in zip(periods, periods[1:]):
            expected_next = (
                (previous[0], previous[1] + 1)
                if previous[1] < 4
                else (previous[0] + 1, 1)
            )
            if current != expected_next:
                continue
            left, left_conf = values[previous]
            right, right_conf = values[current]
            if left_conf < MIN_OPERATIONAL_CONFIDENCE or right_conf < MIN_OPERATIONAL_CONFIDENCE:
                continue
            if left <= 0 or right <= 0 or _ratio(left, right) > 20.0:
                rejected.update({previous, current})

        # order_backlog 업데이트
        for (year, quarter), (amount, _) in values.items():
            if (year, quarter) in rejected:
                # NULL 처리 (이미 NULL일 수 있음)
                if not dry_run:
                    conn.execute(
                        """
                        UPDATE order_backlog
                        SET backlog_amount = NULL, backlog_unit = NULL,
                            backlog_normalized = NULL, backlog_to_rev = NULL,
                            collected_at = CURRENT_TIMESTAMP
                        WHERE stock_code = %s AND year = %s AND quarter = %s
                        """,
                        (stock_code, year, quarter),
                    )
                rejected_total += 1
            else:
                if not dry_run:
                    result = conn.execute(
                        """
                        UPDATE order_backlog
                        SET backlog_amount = %s,
                            backlog_unit = '원',
                            backlog_normalized = %s,
                            data_source = 'dart_backlog',
                            collected_at = CURRENT_TIMESTAMP
                        WHERE stock_code = %s AND year = %s AND quarter = %s
                          AND (backlog_amount IS NULL)
                        """,
                        (amount, amount / 1_000_000.0, stock_code, year, quarter),
                    )
                    if result.rowcount > 0:
                        restored_total += 1
                accepted_total += 1

    if not dry_run:
        conn.commit()

    print(f"\norder_backlog 결과:")
    print(f"  수용(accepted): {accepted_total}건")
    print(f"  거부(rejected by ratio): {rejected_total}건")
    print(f"  실제 복원(NULL → 값): {restored_total}건")

    # 4) data_fix_log 기록
    if not dry_run:
        conn.execute(
            """
            INSERT INTO data_fix_log
              (table_name, scope, row_count, fix_rule, old_value_summary,
               new_value_summary, source, run_id, created_at)
            VALUES
              ('dart_backlog_quarterly + order_backlog', '신뢰도 재산정', %s,
               '파서 신뢰도 과소책정 수정: 0.85→0.96/0.92→0.95/0.90→0.95',
               'dart_backlog_quarterly.backlog_confidence 0.85~0.94 행',
               %s, 'backfill_script', %s, CURRENT_TIMESTAMP)
            """,
            (
                total_updated_conf,
                f"dart_backlog_quarterly conf 업데이트 {total_updated_conf}건, order_backlog NULL→값 복원 {restored_total}건",
                RUN_ID,
            ),
        )
        conn.commit()
        print(f"\ndata_fix_log 기록 완료 (run_id: {RUN_ID})")

    conn.close()
    print("\n완료.")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.dry_run:
        print("[DRY-RUN 모드 — DB 변경 없음]")
    main(dry_run=args.dry_run)
