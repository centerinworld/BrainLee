"""
corporate_action_events review_required 건 일괄 처리 스크립트 (2026-10-01 / 2026-10-02 추가)

변경 내역 (2026-10-01):
1. rights_issue + rights_or_other_issue (1,144건): adjustment_status = 'not_price_adjusting'
2. bonus_issue (4건): backward_price_factor = 1/(1+share_ratio), adjustment_status = 'factor_confirmed'

변경 내역 (2026-10-02 추가):
3. reduction_or_cancellation (19건): not_price_adjusting — 출자전환/소각은 가격 역조정 불필요
4. stock_split review_required (2건): factor_confirmed — ratio>1은 bpf=1/ratio
5. stock_merge_or_reduction (115건):
   - share_ratio > 1 (병합·주식수 증가형): factor_confirmed, bpf=1/share_ratio
   - share_ratio <= 1 또는 NULL (병합·감자형): not_price_adjusting (가격단절 미적용)
6. share_increase_unclassified (502건):
   - price_history 가격 비율(post/prev) > 0.90: not_price_adjusting (가격 변동 미미)
   - price_history 가격 비율 < 0.60: factor_confirmed, bpf = actual_ratio
   - 데이터 없거나 0.60~0.90 사이: not_price_adjusting (보수적 처리)

실행:
    python3 scripts/fix_corp_action_review_required_20261001.py [--dry-run] [--step N]
    (--step 0: 2026-10-01 원본, --step 1~4: 2026-10-02 추가 단계별 실행, 미지정 시 전체 실행)
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db_compat


def _run_step3_reduction_or_cancellation(cur, dry_run: bool) -> int:
    """reduction_or_cancellation → not_price_adjusting."""
    cur.execute("""
        SELECT COUNT(*) FROM corporate_action_events
        WHERE event_type = 'reduction_or_cancellation'
          AND adjustment_status = 'review_required'
    """)
    cnt = cur.fetchone()[0]
    print(f"[3] reduction_or_cancellation review_required: {cnt}건 → not_price_adjusting")
    if not dry_run and cnt > 0:
        cur.execute("""
            UPDATE corporate_action_events
            SET adjustment_status = 'not_price_adjusting', updated_at = NOW()
            WHERE event_type = 'reduction_or_cancellation'
              AND adjustment_status = 'review_required'
        """)
    return cnt


def _run_step4_stock_split(cur, dry_run: bool) -> int:
    """stock_split review_required (ratio>1) → factor_confirmed."""
    cur.execute("""
        SELECT id, stock_code, event_date, share_ratio
        FROM corporate_action_events
        WHERE event_type = 'stock_split'
          AND adjustment_status = 'review_required'
    """)
    rows = cur.fetchall()
    print(f"[4] stock_split review_required: {len(rows)}건")
    confirmed = 0
    for row in rows:
        row_id, code, date, ratio = row[0], row[1], row[2], float(row[3] or 0)
        if ratio and ratio != 1:
            bpf = 1.0 / ratio
            print(f"  {code} {date}: ratio={ratio:.6f} → bpf={bpf:.6f} factor_confirmed")
            if not dry_run:
                cur.execute("""
                    UPDATE corporate_action_events
                    SET backward_price_factor=%s, adjustment_status='factor_confirmed', updated_at=NOW()
                    WHERE id=%s
                """, (bpf, row_id))
            confirmed += 1
        else:
            print(f"  {code} {date}: ratio={ratio} → not_price_adjusting (ratio=1 또는 NULL)")
            if not dry_run:
                cur.execute("""
                    UPDATE corporate_action_events
                    SET adjustment_status='not_price_adjusting', updated_at=NOW()
                    WHERE id=%s
                """, (row_id,))
    return len(rows)


def _run_step5_stock_merge_or_reduction(cur, dry_run: bool) -> int:
    """stock_merge_or_reduction: ratio>1 → factor_confirmed, else → not_price_adjusting."""
    cur.execute("""
        SELECT id, stock_code, event_date, share_ratio
        FROM corporate_action_events
        WHERE event_type = 'stock_merge_or_reduction'
          AND adjustment_status = 'review_required'
        ORDER BY event_date, stock_code
    """)
    rows = cur.fetchall()
    print(f"[5] stock_merge_or_reduction review_required: {len(rows)}건")
    confirmed = 0
    not_adj = 0
    for row in rows:
        row_id, code, date, ratio = row[0], row[1], row[2], float(row[3] or 0)
        if ratio and ratio > 1:
            bpf = 1.0 / ratio
            if not dry_run:
                cur.execute("""
                    UPDATE corporate_action_events
                    SET backward_price_factor=%s, adjustment_status='factor_confirmed', updated_at=NOW()
                    WHERE id=%s
                """, (bpf, row_id))
            confirmed += 1
        else:
            if not dry_run:
                cur.execute("""
                    UPDATE corporate_action_events
                    SET adjustment_status='not_price_adjusting', updated_at=NOW()
                    WHERE id=%s
                """, (row_id,))
            not_adj += 1
    print(f"  → factor_confirmed {confirmed}건, not_price_adjusting {not_adj}건")
    return len(rows)


def _run_step6_share_increase_unclassified(cur, dry_run: bool) -> dict:
    """share_increase_unclassified: price_history 비율로 교차검증 분류."""
    cur.execute("""
        SELECT id, stock_code, event_date, share_ratio
        FROM corporate_action_events
        WHERE event_type = 'share_increase_unclassified'
          AND adjustment_status = 'review_required'
        ORDER BY event_date, stock_code
    """)
    rows = cur.fetchall()
    print(f"[6] share_increase_unclassified review_required: {len(rows)}건 → 교차검증")
    stats = {"factor_confirmed": 0, "not_price_adjusting": 0, "no_data": 0}

    for row in rows:
        row_id, code, event_date, share_ratio = row[0], row[1], str(row[2])[:10], row[3]

        # event_date 전날, 당일 또는 다음날 종가 비교 (권리락 날짜 불확실성 감안 ±3영업일)
        # price_history.date는 text 타입이므로 date::date 캐스팅 후 비교
        cur.execute("""
            SELECT date, close
            FROM price_history
            WHERE stock_code=%s AND close>0
              AND date::date BETWEEN (%s::date - INTERVAL '3 days') AND (%s::date + INTERVAL '3 days')
            ORDER BY date
        """, (code, event_date, event_date))
        price_rows = cur.fetchall()

        prev_close = next_close = None
        for pr in price_rows:
            pr_date = str(pr[0])[:10]
            if pr_date < event_date:
                prev_close = float(pr[1])
            elif pr_date > event_date and next_close is None:
                next_close = float(pr[1])

        if not prev_close or not next_close:
            # 데이터 없으면 보수적으로 not_price_adjusting
            if not dry_run:
                cur.execute("""
                    UPDATE corporate_action_events
                    SET adjustment_status='not_price_adjusting', updated_at=NOW()
                    WHERE id=%s
                """, (row_id,))
            stats["no_data"] += 1
            continue

        actual_ratio = next_close / prev_close

        if actual_ratio < 0.60:
            # 큰 가격 하락 → factor 필요
            bpf = actual_ratio  # 실제 가격비율을 bpf로 사용
            if not dry_run:
                cur.execute("""
                    UPDATE corporate_action_events
                    SET backward_price_factor=%s, adjustment_status='factor_confirmed', updated_at=NOW()
                    WHERE id=%s
                """, (bpf, row_id))
            stats["factor_confirmed"] += 1
        else:
            # actual_ratio >= 0.60: 가격 변동 미미하거나 불명확 → not_price_adjusting
            if not dry_run:
                cur.execute("""
                    UPDATE corporate_action_events
                    SET adjustment_status='not_price_adjusting', updated_at=NOW()
                    WHERE id=%s
                """, (row_id,))
            stats["not_price_adjusting"] += 1

    print(f"  → factor_confirmed {stats['factor_confirmed']}건, "
          f"not_price_adjusting {stats['not_price_adjusting']}건, "
          f"no_data(→not_price_adjusting) {stats['no_data']}건")
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="실제 변경 없이 확인만")
    parser.add_argument("--step", type=int, default=None,
                        help="0=원본(2026-10-01), 1=step3, 2=step4, 3=step5, 4=step6, 미지정=전체")
    args = parser.parse_args()

    conn = db_compat.connect_primary_db()
    cur = conn.cursor()

    # ── 1. rights_issue + rights_or_other_issue → not_price_adjusting ──────────
    cur.execute("""
        SELECT COUNT(*) FROM corporate_action_events
        WHERE event_type IN ('rights_issue', 'rights_or_other_issue')
          AND adjustment_status = 'review_required'
    """)
    rights_count = cur.fetchone()[0]
    print(f"[1] rights_issue/rights_or_other_issue review_required: {rights_count:,}건")

    if not args.dry_run and rights_count > 0:
        cur.execute("""
            UPDATE corporate_action_events
            SET adjustment_status = 'not_price_adjusting',
                updated_at = NOW()
            WHERE event_type IN ('rights_issue', 'rights_or_other_issue')
              AND adjustment_status = 'review_required'
        """)
        print(f"  → not_price_adjusting 업데이트 완료 ({cur.rowcount}건)")

    # ── 2. bonus_issue → factor 계산 후 factor_confirmed ─────────────────────
    cur.execute("""
        SELECT id, stock_code, event_date, share_ratio
        FROM corporate_action_events
        WHERE event_type = 'bonus_issue'
          AND adjustment_status = 'review_required'
          AND share_ratio IS NOT NULL AND share_ratio > 0
    """)
    bonus_rows = cur.fetchall()
    print(f"\n[2] bonus_issue review_required (share_ratio 있음): {len(bonus_rows)}건")

    for row in bonus_rows:
        row_id = row[0]
        code = row[1]
        date = row[2]
        ratio = float(row[3])
        factor = 1.0 / (1.0 + ratio)
        print(f"  {code} {date}: share_ratio={ratio:.6f} → backward_price_factor={factor:.6f}")

        if not args.dry_run:
            cur.execute("""
                UPDATE corporate_action_events
                SET backward_price_factor = %s,
                    adjustment_status = 'factor_confirmed',
                    updated_at = NOW()
                WHERE id = %s
            """, (factor, row_id))

    run_original = args.step is None or args.step == 0
    run_new = args.step is None or args.step in (1, 2, 3, 4)

    if run_original:
        if not args.dry_run and rights_count > 0:
            print("  (2026-10-01 Step 1,2는 이미 실행됨 — review_required=0이면 skip)")

    if run_new:
        if args.step is None or args.step == 1:
            _run_step3_reduction_or_cancellation(cur, args.dry_run)
        if args.step is None or args.step == 2:
            _run_step4_stock_split(cur, args.dry_run)
        if args.step is None or args.step == 3:
            _run_step5_stock_merge_or_reduction(cur, args.dry_run)
        if args.step is None or args.step == 4:
            _run_step6_share_increase_unclassified(cur, args.dry_run)

    if not args.dry_run:
        conn.commit()
        print("\n✅ 커밋 완료")
    else:
        print("\n[dry-run] 변경 없음 — --dry-run 없이 실행하면 적용됩니다.")

    conn.close()


if __name__ == "__main__":
    main()
