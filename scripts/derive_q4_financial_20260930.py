"""
derive_q4_financial_20260930.py
Q4 재무 파생: Q4 = Annual - Q1 - Q2 - Q3 (흐름 필드)
              Q4 = Annual 값 (BS 필드: total_assets, total_equity)

두 가지 처리:
1. Q4 행이 없는 종목-연도: INSERT
2. Q4 행이 있지만 NULL인 종목-연도: UPDATE

RUN_ID: q4_rederive_20260930
"""
from __future__ import annotations
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db_compat import connect_primary_db

RUN_ID = "q4_rederive_20260930"

def main():
    conn = connect_primary_db(timeout=120)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"=== Q4 재무 파생 시작 ({now_str}) ===")

    # 파생 가능한 전체 목록 수집
    # annual: (stock_code, year, report_type) 기준 중복 제거 (quarter=0 우선)
    # q123: report_type별로 집계해 annual과 동일 report_type 매칭
    rows = conn.execute("""
        WITH annual AS (
            SELECT DISTINCT ON (stock_code, year, report_type)
                   stock_code, year,
                   revenue, operating_profit, net_income,
                   total_assets, total_equity,
                   report_type, data_source
            FROM financial_data WHERE is_annual=true
            ORDER BY stock_code, year, report_type,
                     CASE WHEN quarter=0 THEN 0 WHEN quarter IS NULL THEN 1 ELSE 2 END
        ),
        q123 AS (
            SELECT stock_code, year, report_type,
                SUM(CASE WHEN quarter=1 THEN revenue END) as rev_q1,
                SUM(CASE WHEN quarter=2 THEN revenue END) as rev_q2,
                SUM(CASE WHEN quarter=3 THEN revenue END) as rev_q3,
                SUM(CASE WHEN quarter=1 THEN operating_profit END) as op_q1,
                SUM(CASE WHEN quarter=2 THEN operating_profit END) as op_q2,
                SUM(CASE WHEN quarter=3 THEN operating_profit END) as op_q3,
                SUM(CASE WHEN quarter=1 THEN net_income END) as ni_q1,
                SUM(CASE WHEN quarter=2 THEN net_income END) as ni_q2,
                SUM(CASE WHEN quarter=3 THEN net_income END) as ni_q3
            FROM financial_data WHERE is_annual=false AND quarter IN (1,2,3)
            GROUP BY stock_code, year, report_type
        ),
        q4_existing AS (
            SELECT id, stock_code, year, report_type,
                   revenue, operating_profit, net_income,
                   total_assets, total_equity
            FROM financial_data WHERE is_annual=false AND quarter=4
        )
        SELECT
            a.stock_code, a.year,
            a.revenue as ann_rev, q.rev_q1, q.rev_q2, q.rev_q3,
            a.operating_profit as ann_op, q.op_q1, q.op_q2, q.op_q3,
            a.net_income as ann_ni, q.ni_q1, q.ni_q2, q.ni_q3,
            a.total_assets, a.total_equity,
            a.report_type, a.data_source,
            q4.id as q4_id,
            q4.revenue as q4_rev_cur, q4.operating_profit as q4_op_cur,
            q4.net_income as q4_ni_cur, q4.total_assets as q4_ta_cur,
            q4.total_equity as q4_te_cur
        FROM annual a
        JOIN q123 q ON a.stock_code=q.stock_code AND a.year=q.year
                    AND a.report_type=q.report_type
        LEFT JOIN q4_existing q4 ON q4.stock_code=a.stock_code AND q4.year=a.year
                                 AND q4.report_type=a.report_type
        WHERE a.revenue IS NOT NULL
          AND q.rev_q1 IS NOT NULL AND q.rev_q2 IS NOT NULL AND q.rev_q3 IS NOT NULL
    """).fetchall()

    inserted = 0
    updated = 0
    skipped = 0

    ts = datetime.now(timezone.utc).isoformat()

    for row in rows:
        (code, year,
         ann_rev, rev_q1, rev_q2, rev_q3,
         ann_op, op_q1, op_q2, op_q3,
         ann_ni, ni_q1, ni_q2, ni_q3,
         total_assets, total_equity,
         report_type, data_source,
         q4_id, q4_rev_cur, q4_op_cur, q4_ni_cur, q4_ta_cur, q4_te_cur) = row

        # 파생값 계산
        q4_rev = ann_rev - rev_q1 - rev_q2 - rev_q3
        q4_op  = (ann_op - op_q1 - op_q2 - op_q3) if (ann_op is not None and op_q1 is not None and op_q2 is not None and op_q3 is not None) else None
        q4_ni  = (ann_ni - ni_q1 - ni_q2 - ni_q3) if (ann_ni is not None and ni_q1 is not None and ni_q2 is not None and ni_q3 is not None) else None
        q4_ta  = total_assets   # BS: Q4 = Annual 값
        q4_te  = total_equity   # BS: Q4 = Annual 값

        ds = f"q4_derived_{data_source}" if data_source else "q4_derived"

        if q4_id is None:
            # 신규 삽입
            conn.execute("""
                INSERT INTO financial_data
                (stock_code, year, quarter, is_annual, revenue, operating_profit,
                 net_income, total_assets, total_equity, report_type, data_source)
                VALUES (%s, %s, 4, false, %s, %s, %s, %s, %s, %s, %s)
            """, (code, year, q4_rev, q4_op, q4_ni, q4_ta, q4_te, report_type, ds))
            inserted += 1
        else:
            # 기존 행 업데이트 (NULL인 필드만)
            updates = {}
            if q4_rev_cur is None: updates['revenue'] = q4_rev
            if q4_op_cur is None and q4_op is not None: updates['operating_profit'] = q4_op
            if q4_ni_cur is None and q4_ni is not None: updates['net_income'] = q4_ni
            if q4_ta_cur is None and q4_ta is not None: updates['total_assets'] = q4_ta
            if q4_te_cur is None and q4_te is not None: updates['total_equity'] = q4_te

            if not updates:
                skipped += 1
                continue

            set_clause = ', '.join(f"{k}=%s" for k in updates)
            vals = list(updates.values()) + [q4_id]
            conn.execute(f"UPDATE financial_data SET {set_clause} WHERE id=%s", vals)
            updated += 1

    conn.commit()

    print(f"  신규 삽입: {inserted}건")
    print(f"  NULL 업데이트: {updated}건")
    print(f"  변경 없음(스킵): {skipped}건")

    # data_fix_log 기록
    conn.execute("""
        INSERT INTO data_fix_log
        (table_name, scope, row_count, fix_rule, old_value_summary, new_value_summary, source, run_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        'financial_data',
        'Q4 quarterly rows 2016-2025',
        inserted + updated,
        'Q4=Annual-Q1-Q2-Q3 (flow); Q4=Annual (BS)',
        f'inserted={inserted}, updated={updated}, skipped={skipped}',
        'revenue/op_profit/net_income=derived; total_assets/equity=annual_copy',
        'internal_derivation',
        RUN_ID
    ))
    conn.commit()

    # 완료 후 결측 현황
    r = conn.execute("""
        SELECT
            SUM(CASE WHEN revenue IS NULL THEN 1 ELSE 0 END) as rev_null,
            SUM(CASE WHEN operating_profit IS NULL THEN 1 ELSE 0 END) as op_null,
            SUM(CASE WHEN net_income IS NULL THEN 1 ELSE 0 END) as ni_null,
            SUM(CASE WHEN total_assets IS NULL THEN 1 ELSE 0 END) as ta_null,
            SUM(CASE WHEN total_equity IS NULL THEN 1 ELSE 0 END) as te_null
        FROM financial_data WHERE is_annual=false AND quarter=4
    """).fetchone()
    print(f"\n완료 후 Q4 결측:")
    print(f"  revenue={r[0]}, op_profit={r[1]}, net_income={r[2]}")
    print(f"  total_assets={r[3]}, total_equity={r[4]}")

    conn.close()
    print(f"\n=== 완료 (run_id={RUN_ID}) ===")

if __name__ == "__main__":
    main()
