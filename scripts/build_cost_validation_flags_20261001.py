"""
scripts/build_cost_validation_flags_20261001.py

dart_cost_quarterly_validation_flags 테이블 생성 + 전체 채우기

검사 항목:
  1. 파서 품질 등급 (conf 기반)
     - conf ≥ 0.85 (non_null=3) → HIGH
     - 0.65 ≤ conf < 0.85 (non_null=2) → MED
     - conf < 0.65 (non_null=1) → LOW
  2. 감가상각 cf 교차검증
     - cf.depreciation 대비 ratio < 0.01 → UNIT_ERROR_LIKELY
     - ratio > 5.0 → OVERSIZE_WARN
     - 0.01 ≤ ratio ≤ 5.0 → CROSS_CHECK_OK
  3. 재료비 매출 비율
     - material_cost_krw / revenue > 2.0 → RATIO_ANOMALY
     - ≤ 2.0 → OK
  4. 감가상각 YoY 극단값
     - |yoy| > 500% → YOY_SPIKE
  5. overall_flag
     - FAIL: UNIT_ERROR_LIKELY 또는 RATIO_ANOMALY
     - WARN: OVERSIZE_WARN 또는 YOY_SPIKE
     - PASS: 그 외

run_id: build_cost_vflags_20261001
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db_compat import connect_primary_db

RUN_ID = "build_cost_vflags_20261001"
BATCH = 2000


def _create_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dart_cost_quarterly_validation_flags (
            stock_code      TEXT    NOT NULL,
            fiscal_year     INTEGER NOT NULL,
            fiscal_quarter  INTEGER NOT NULL,
            report_type     TEXT    NOT NULL DEFAULT 'CFS',

            parser_quality  TEXT,   -- HIGH / MED / LOW

            dep_cf_ratio    REAL,
            dep_cf_flag     TEXT,   -- CROSS_CHECK_OK / UNIT_ERROR_LIKELY / OVERSIZE_WARN / NO_CF_DATA / NO_DEP_DATA

            mat_rev_ratio   REAL,
            mat_rev_flag    TEXT,   -- OK / RATIO_ANOMALY / NO_REV_DATA / NO_MAT_DATA

            dep_yoy_pct     REAL,
            dep_yoy_flag    TEXT,   -- OK / YOY_SPIKE / NO_PREV_DATA

            overall_flag    TEXT,   -- PASS / WARN / FAIL

            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            PRIMARY KEY (stock_code, fiscal_year, fiscal_quarter, report_type)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_dcqvf_overall
        ON dart_cost_quarterly_validation_flags(overall_flag)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_dcqvf_quality
        ON dart_cost_quarterly_validation_flags(parser_quality)
    """)
    conn.commit()


def _parser_quality(conf: float) -> str:
    if conf >= 0.85:
        return "HIGH"
    elif conf >= 0.65:
        return "MED"
    else:
        return "LOW"


def _dep_cf_flag(dep: float | None, cf_dep: float | None) -> tuple[float | None, str]:
    if dep is None:
        return None, "NO_DEP_DATA"
    if cf_dep is None or cf_dep <= 1_000_000:
        return None, "NO_CF_DATA"
    ratio = dep / cf_dep
    if ratio < 0.01:
        return ratio, "UNIT_ERROR_LIKELY"
    elif ratio > 5.0:
        return ratio, "OVERSIZE_WARN"
    else:
        return ratio, "CROSS_CHECK_OK"


def _mat_rev_flag(mat: float | None, rev: float | None) -> tuple[float | None, str]:
    if mat is None:
        return None, "NO_MAT_DATA"
    if rev is None or rev <= 0:
        return None, "NO_REV_DATA"
    ratio = mat / rev
    if ratio > 2.0:
        return ratio, "RATIO_ANOMALY"
    else:
        return ratio, "OK"


def _dep_yoy_flag(yoy: float | None) -> str:
    if yoy is None:
        return "NO_PREV_DATA"
    if abs(yoy) > 500.0:
        return "YOY_SPIKE"
    return "OK"


def _overall(dcf: str, mrf: str, dyf: str) -> str:
    if dcf == "UNIT_ERROR_LIKELY" or mrf == "RATIO_ANOMALY":
        return "FAIL"
    if dcf == "OVERSIZE_WARN" or dyf == "YOY_SPIKE":
        return "WARN"
    return "PASS"


def main(dry_run: bool = False) -> None:
    conn = connect_primary_db(timeout=300)
    conn.execute("SET statement_timeout = '600s'")

    _create_table(conn)
    print("테이블 준비 완료")

    # dart_cost_quarterly 전체 행 로드 (배치 처리)
    total_rows = conn.execute("SELECT COUNT(*) FROM dart_cost_quarterly").fetchone()[0]
    print(f"처리 대상: {total_rows:,}건")

    # cf 및 revenue 조인 데이터 미리 로드 (메모리 효율을 위해 dict로)
    print("cf 데이터 로드 중...")
    cf_map: dict[tuple, float] = {}
    for r in conn.execute("""
        SELECT stock_code, year, quarter, depreciation
        FROM cash_flow_data
        WHERE depreciation IS NOT NULL AND depreciation > 1000000
    """).fetchall():
        cf_map[(r[0], r[1], r[2])] = float(r[3])

    print(f"cf 데이터: {len(cf_map):,}건")

    print("revenue 데이터 로드 중...")
    rev_map: dict[tuple, float] = {}
    for r in conn.execute("""
        SELECT stock_code, year, quarter, revenue
        FROM financial_data
        WHERE revenue IS NOT NULL AND revenue > 0
    """).fetchall():
        rev_map[(r[0], r[1], r[2])] = float(r[3])

    print(f"revenue 데이터: {len(rev_map):,}건")

    # YoY 계산을 위해 depreciation 시계열 미리 구성
    print("depreciation 시계열 로드 중...")
    dep_series: dict[tuple, dict[tuple, float]] = {}  # (sc, rt) → {(fy, fq): dep}
    for r in conn.execute("""
        SELECT stock_code, fiscal_year, fiscal_quarter, report_type, depreciation_krw
        FROM dart_cost_quarterly
        WHERE depreciation_krw IS NOT NULL
        ORDER BY stock_code, report_type, fiscal_year, fiscal_quarter
    """).fetchall():
        key = (r[0], r[3])
        if key not in dep_series:
            dep_series[key] = {}
        dep_series[key][(r[1], r[2])] = float(r[4])

    # 전체 행 처리
    offset = 0
    processed = 0
    fail_cnt = warn_cnt = pass_cnt = 0

    while True:
        rows = conn.execute("""
            SELECT stock_code, fiscal_year, fiscal_quarter, report_type,
                   material_cost_krw, depreciation_krw, confidence
            FROM dart_cost_quarterly
            ORDER BY stock_code, fiscal_year, fiscal_quarter
            LIMIT %s OFFSET %s
        """, (BATCH, offset)).fetchall()

        if not rows:
            break

        batch_data = []
        for sc, fy, fq, rt, mat, dep, conf in rows:
            fy, fq = int(fy), int(fq)
            conf = float(conf or 0)

            pq = _parser_quality(conf)

            cf_dep = cf_map.get((sc, fy, fq))
            dep_ratio, dcf = _dep_cf_flag(dep, cf_dep)

            rev = rev_map.get((sc, fy, fq))
            mat_ratio, mrf = _mat_rev_flag(mat, rev)

            # YoY
            series = dep_series.get((sc, rt), {})
            prev_y = series.get((fy - 1, fq))
            yoy = None
            if dep is not None and prev_y and prev_y != 0:
                yoy = (dep - prev_y) / abs(prev_y) * 100
            dyf = _dep_yoy_flag(yoy)

            overall = _overall(dcf, mrf, dyf)
            if overall == "FAIL":
                fail_cnt += 1
            elif overall == "WARN":
                warn_cnt += 1
            else:
                pass_cnt += 1

            batch_data.append((
                sc, fy, fq, rt,
                pq,
                dep_ratio, dcf,
                mat_ratio, mrf,
                yoy, dyf,
                overall,
            ))

        if not dry_run:
            for row in batch_data:
                conn.execute("""
                    INSERT INTO dart_cost_quarterly_validation_flags
                        (stock_code, fiscal_year, fiscal_quarter, report_type,
                         parser_quality, dep_cf_ratio, dep_cf_flag,
                         mat_rev_ratio, mat_rev_flag, dep_yoy_pct, dep_yoy_flag,
                         overall_flag)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (stock_code, fiscal_year, fiscal_quarter, report_type)
                    DO UPDATE SET
                        parser_quality = EXCLUDED.parser_quality,
                        dep_cf_ratio   = EXCLUDED.dep_cf_ratio,
                        dep_cf_flag    = EXCLUDED.dep_cf_flag,
                        mat_rev_ratio  = EXCLUDED.mat_rev_ratio,
                        mat_rev_flag   = EXCLUDED.mat_rev_flag,
                        dep_yoy_pct    = EXCLUDED.dep_yoy_pct,
                        dep_yoy_flag   = EXCLUDED.dep_yoy_flag,
                        overall_flag   = EXCLUDED.overall_flag,
                        updated_at     = NOW()
                """, row)
            conn.commit()

        processed += len(rows)
        offset += BATCH
        print(f"  진행: {processed:,}/{total_rows:,} (FAIL={fail_cnt}, WARN={warn_cnt}, PASS={pass_cnt})")

    print(f"\n=== 최종 결과 ===")
    print(f"전체 처리: {processed:,}건")
    print(f"  PASS:  {pass_cnt:,}건")
    print(f"  WARN:  {warn_cnt:,}건")
    print(f"  FAIL:  {fail_cnt:,}건")

    if not dry_run:
        # 집계 확인
        dist = conn.execute("""
            SELECT overall_flag, parser_quality, COUNT(*)
            FROM dart_cost_quarterly_validation_flags
            GROUP BY overall_flag, parser_quality
            ORDER BY overall_flag, parser_quality
        """).fetchall()
        print("\n=== DB 최종 분포 ===")
        for row in dist:
            print(f"  overall={row[0]}, quality={row[1]}: {row[2]:,}건")

        # dep_cf_flag 분포
        cf_dist = conn.execute("""
            SELECT dep_cf_flag, COUNT(*)
            FROM dart_cost_quarterly_validation_flags
            GROUP BY dep_cf_flag ORDER BY COUNT(*) DESC
        """).fetchall()
        print("\n=== dep_cf_flag 분포 ===")
        for row in cf_dist:
            print(f"  {row[0]}: {row[1]:,}건")

        conn.execute("""
            INSERT INTO data_fix_log
              (table_name, scope, row_count, fix_rule,
               old_value_summary, new_value_summary, source, run_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            "dart_cost_quarterly_validation_flags",
            "신규 테이블 생성 + 전체 채우기",
            processed,
            "cf 교차검증 / 매출 비율 / YoY 극단값 / 파서 신뢰도 등급",
            "없음(신규)",
            f"PASS={pass_cnt}, WARN={warn_cnt}, FAIL={fail_cnt}",
            "build_script",
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
