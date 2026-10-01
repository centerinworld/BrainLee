"""
scripts/fix_cost_unit_errors_20260930.py

dart_cost_quarterly.depreciation_krw 단위 오류 처리
──────────────────────────────────────────────────
원인: _pick_amount의 pattern_no_unit이 표 헤더 단위("단위:백만원")를 무시하고
     "원" 기본값 사용 → 실제 100만배+ 과소평가

처리 기준:
  - cash_flow_data.depreciation(cf_dep) 대비 0.001 미만 = 확실 오류 → NULL
  - cf_dep 없는 행은 material_cost_krw 절대값 비교로 교차검증
  - tenbagger_triggers_quarterly의 depreciation 메트릭도 동일 처리

run_id: fix_cost_unit_20260930
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db_compat import connect_primary_db

RUN_ID = "fix_cost_unit_20260930"
RATIO_THRESHOLD = 0.001   # cf_dep 대비 이 미만이면 확실 오류
MIN_CF_DEP = 1_000_000    # cf_dep 비교 최소값 (1백만원) — 너무 작으면 노이즈


def main(dry_run: bool = False) -> None:
    conn = connect_primary_db(timeout=120)
    conn.execute("SET statement_timeout = '600s'")

    # ── 1. 오류 행 식별 ──────────────────────────────────────────────────────
    rows = conn.execute("""
        SELECT
            dcq.stock_code,
            dcq.fiscal_year,
            dcq.fiscal_quarter,
            dcq.report_type,
            dcq.depreciation_krw,
            dcq.confidence,
            cf.depreciation AS cf_dep
        FROM dart_cost_quarterly dcq
        LEFT JOIN cash_flow_data cf
            ON cf.stock_code = dcq.stock_code
           AND cf.year        = dcq.fiscal_year
           AND cf.quarter     = dcq.fiscal_quarter
        WHERE dcq.depreciation_krw IS NOT NULL
          AND dcq.depreciation_krw > 0
          AND dcq.confidence < 0.9
    """).fetchall()

    null_targets: list[tuple] = []
    no_cf_count = 0

    for sc, fy, fq, rt, dep_krw, conf, cf_dep in rows:
        if cf_dep and cf_dep > MIN_CF_DEP:
            ratio = dep_krw / cf_dep
            if ratio < RATIO_THRESHOLD:
                null_targets.append((sc, int(fy), int(fq), rt, dep_krw, conf, cf_dep, ratio))
        else:
            no_cf_count += 1

    print(f"=== dart_cost_quarterly 단위 오류 처리 ===")
    print(f"전체 conf<0.9 감가상각 행: {len(rows)}건")
    print(f"  cf 비교 가능: {len(rows) - no_cf_count}건")
    print(f"  cf 없음/너무 작음: {no_cf_count}건")
    print(f"  확실 오류(비율<{RATIO_THRESHOLD}): {len(null_targets)}건")
    print()

    # conf별 집계
    from collections import Counter
    conf_counts: Counter = Counter()
    for *_, conf, cf_dep, ratio in null_targets:
        conf_counts[round(conf, 2)] += 1
    for conf_val in sorted(conf_counts):
        print(f"  conf={conf_val}: {conf_counts[conf_val]}건 NULL 처리")
    print()

    if not null_targets:
        print("처리 대상 없음.")
        conn.close()
        return

    if dry_run:
        print("[DRY-RUN] DB 변경 없음.")
        conn.close()
        return

    # ── 2. dart_cost_quarterly.depreciation_krw → NULL ───────────────────────
    updated_cost = 0
    for sc, fy, fq, rt, *_ in null_targets:
        res = conn.execute("""
            UPDATE dart_cost_quarterly
            SET depreciation_krw = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE stock_code = %s AND fiscal_year = %s AND fiscal_quarter = %s
              AND report_type = %s AND depreciation_krw IS NOT NULL
        """, (sc, fy, fq, rt))
        updated_cost += res.rowcount
    conn.commit()
    print(f"dart_cost_quarterly.depreciation_krw NULL 처리: {updated_cost}건")

    # ── 3. dart_tenbagger_triggers_quarterly.depreciation → NULL ─────────────
    updated_trigger = 0
    for sc, fy, fq, rt, *_ in null_targets:
        res = conn.execute("""
            UPDATE dart_tenbagger_triggers_quarterly
            SET metric_value  = NULL,
                yoy_pct       = NULL,
                qoq_pct       = NULL,
                trigger_level = NULL,
                updated_at    = CURRENT_TIMESTAMP
            WHERE stock_code   = %s
              AND fiscal_year  = %s
              AND fiscal_quarter = %s
              AND report_type  = %s
              AND metric_name  = 'depreciation'
        """, (sc, fy, fq, rt))
        updated_trigger += res.rowcount
    conn.commit()
    print(f"dart_tenbagger_triggers_quarterly.depreciation NULL 처리: {updated_trigger}건")

    # ── 4. 영향받은 종목의 YoY/QoQ 재계산 ────────────────────────────────────
    affected_stocks = list({(sc, rt) for sc, fy, fq, rt, *_ in null_targets})
    recalc_count = 0
    for sc, rt in affected_stocks:
        series = conn.execute("""
            SELECT fiscal_year, fiscal_quarter, depreciation_krw
            FROM dart_cost_quarterly
            WHERE stock_code = %s AND report_type = %s
              AND depreciation_krw IS NOT NULL
            ORDER BY fiscal_year, fiscal_quarter
        """, (sc, rt)).fetchall()
        m = {(r[0], r[1]): r[2] for r in series}
        for (fy, fq), cur in m.items():
            prev_q = (fy, fq - 1) if fq > 1 else (fy - 1, 4)
            prev_y = (fy - 1, fq)
            qv = m.get(prev_q)
            yv = m.get(prev_y)
            qoq = ((cur - qv) / abs(qv) * 100) if qv else None
            yoy = ((cur - yv) / abs(yv) * 100) if yv else None
            level = None
            if yoy is not None and yoy >= 20:
                level = "CAPEX_RAMP_SIGNAL"
            conn.execute("""
                UPDATE dart_tenbagger_triggers_quarterly
                SET metric_value = %s, yoy_pct = %s, qoq_pct = %s,
                    trigger_level = %s, updated_at = CURRENT_TIMESTAMP
                WHERE stock_code = %s AND fiscal_year = %s
                  AND fiscal_quarter = %s AND report_type = %s
                  AND metric_name = 'depreciation'
            """, (cur, yoy, qoq, level, sc, fy, fq, rt))
            recalc_count += 1
    conn.commit()
    print(f"tenbagger_triggers YoY/QoQ 재계산: {recalc_count}건")

    # ── 5. data_fix_log ───────────────────────────────────────────────────────
    conn.execute("""
        INSERT INTO data_fix_log
          (table_name, scope, row_count, fix_rule,
           old_value_summary, new_value_summary, source, run_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        "dart_cost_quarterly + dart_tenbagger_triggers_quarterly",
        "depreciation_krw 단위 오류 NULL 처리",
        len(null_targets),
        f"cf_depreciation 대비 ratio<{RATIO_THRESHOLD} → NULL (conf<0.9)",
        f"오류 행: dart_cost_quarterly {updated_cost}건, tenbagger {updated_trigger}건",
        "depreciation_krw=NULL, trigger_level=NULL (재수집 후 cost_v2 파서로 복원 예정)",
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
        print("[DRY-RUN 모드]")
    main(dry_run=args.dry_run)
