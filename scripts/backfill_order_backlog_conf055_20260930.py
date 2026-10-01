"""
scripts/backfill_order_backlog_conf055_20260930.py

_UNIT_TAG_PAT이 "단 위"(글자 공백) 형식을 못 찾던 버그로
신뢰도 0.55로 과소 배정된 행 중 source_excerpt에서 넓은 패턴으로
단위가 확인되고 저장된 backlog_unit과 일치하는 393건을 0.95로 올린다.

run_id: backfill_backlog_conf055_20260930
"""
from __future__ import annotations
import re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db_compat import connect_primary_db

_WIDE_UNIT_PAT = r"\(\s*단\s*위\s*[:：][^)]{0,60}?(조원|억원|백만원|백만|천만원|천원|만원|원)[^)]{0,20}\)"
_UNIT_MAP = {"조원": "조원", "억원": "억원", "백만원": "백만원", "백만": "백만원",
             "천만원": "천만원", "천원": "천원", "만원": "만원", "원": "원"}
MIN_CONF = 0.95
OLD_CONF = 0.55
RUN_ID = "backfill_backlog_conf055_20260930"


def _ratio(a: float, b: float) -> float:
    if a <= 0 or b <= 0:
        return 999.0
    return max(a, b) / min(a, b)


def main(dry_run: bool = False):
    conn = connect_primary_db(timeout=120)
    conn.execute("SET statement_timeout = '300s'")

    # 대상 행 분류
    rows = conn.execute("""
        SELECT stock_code, fiscal_year, fiscal_quarter, report_type,
               backlog_unit, backlog_amount_krw, source_excerpt
        FROM dart_backlog_quarterly
        WHERE backlog_confidence = %s AND backlog_amount_krw > 0
    """, (OLD_CONF,)).fetchall()

    safe = []
    for r in rows:
        sc, fy, fq, rt, bu, amt, ex = r
        ex = str(ex) if ex else ""
        m = re.search(_WIDE_UNIT_PAT, ex)
        if not m:
            continue
        found_unit = _UNIT_MAP.get(m.group(1), m.group(1))
        if found_unit == bu:
            safe.append((sc, int(fy), int(fq), rt))

    print(f"신뢰도 {OLD_CONF} → {MIN_CONF} 안전 업데이트 대상: {len(safe)}건")
    if not safe:
        print("대상 없음, 종료")
        conn.close()
        return

    # 1) dart_backlog_quarterly 신뢰도 업데이트
    if not dry_run:
        for sc, fy, fq, rt in safe:
            conn.execute("""
                UPDATE dart_backlog_quarterly
                SET backlog_confidence = %s
                WHERE stock_code = %s AND fiscal_year = %s AND fiscal_quarter = %s
                  AND report_type = %s AND backlog_confidence = %s
            """, (MIN_CONF, sc, fy, fq, rt, OLD_CONF))
        conn.commit()
    print(f"dart_backlog_quarterly 업데이트: {len(safe)}건")

    # 2) 영향 종목 order_backlog 재판정
    affected = list({(sc, rt) for sc, fy, fq, rt in safe})
    restored = 0
    rejected = 0

    for sc, rt in affected:
        q_rows = conn.execute("""
            SELECT fiscal_year, fiscal_quarter, backlog_amount_krw, backlog_confidence
            FROM dart_backlog_quarterly
            WHERE stock_code = %s AND report_type = %s
              AND backlog_amount_krw IS NOT NULL
            ORDER BY fiscal_year, fiscal_quarter
        """, (sc, rt)).fetchall()
        if not q_rows:
            continue

        values = {(int(r[0]), int(r[1])): (float(r[2]), float(r[3] or 0)) for r in q_rows}
        rejected_set: set[tuple] = set()
        for period, (amount, conf) in values.items():
            if conf < MIN_CONF or amount <= 0:
                rejected_set.add(period)

        periods = sorted(values)
        for prev, cur in zip(periods, periods[1:]):
            exp = (prev[0], prev[1] + 1) if prev[1] < 4 else (prev[0] + 1, 1)
            if cur != exp:
                continue
            lv, lc = values[prev]
            rv, rc = values[cur]
            if lc < MIN_CONF or rc < MIN_CONF:
                continue
            if lv <= 0 or rv <= 0 or _ratio(lv, rv) > 20.0:
                rejected_set.update({prev, cur})

        for (fy, fq), (amount, _) in values.items():
            if (fy, fq) not in rejected_set:
                if not dry_run:
                    res = conn.execute("""
                        UPDATE order_backlog
                        SET backlog_amount = %s,
                            backlog_unit = '원',
                            backlog_normalized = %s,
                            data_source = 'dart_backlog',
                            collected_at = CURRENT_TIMESTAMP
                        WHERE stock_code = %s AND year = %s AND quarter = %s
                          AND backlog_amount IS NULL
                    """, (amount, amount / 1_000_000.0, sc, fy, fq))
                    if res.rowcount > 0:
                        restored += 1
            else:
                rejected += 1

    if not dry_run:
        conn.commit()

    print(f"order_backlog NULL→값 복원: {restored}건")
    print(f"거부(20배 불연속): {rejected}건")

    # 3) data_fix_log
    if not dry_run:
        conn.execute("SELECT setval('data_fix_log_id_seq', (SELECT MAX(id) FROM data_fix_log))")
        conn.execute("""
            INSERT INTO data_fix_log
              (table_name, scope, row_count, fix_rule,
               old_value_summary, new_value_summary, source, run_id)
            VALUES
              ('dart_backlog_quarterly + order_backlog', '신뢰도 재산정 0.55→0.95', %s,
               '단 위 공백 버그: _UNIT_TAG_PAT 미인식 → 단\\s*위 패턴으로 재검증',
               'backlog_confidence=0.55 중 excerpt 단위 일치 확인',
               %s, 'backfill_script', %s)
        """, (len(safe),
              f"dart_backlog_quarterly {len(safe)}건, order_backlog NULL→값 복원 {restored}건",
              RUN_ID))
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
