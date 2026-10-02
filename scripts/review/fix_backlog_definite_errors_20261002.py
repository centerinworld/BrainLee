#!/usr/bin/env python3
"""수주잔고 '확실한 오류'만 NULL 처리하고, 나머지 의심 행은 검토 플래그로 남긴다(2026-10-02 독립 재검토).

배경: 수주잔고 파서(collectors/dart_backlog_collector.py)의 단위 패턴에 외화(USD 등)가 없어
'(단위 : USD)' 표를 원화로 읽었다(예: 011000 2022Q1 USD 6,829,688 → 6.8조원, 매출의 572배).
또 원문에 없는 숫자를 채택한 행(예: 062040 2026Q2 원문 556,788백만원 → 저장 60,879,895백만원)이 있다.

NULL 처리 기준(둘 중 하나):
  A. 원문 발췌에 외화 단위 선언만 있고 원화 단위 선언이 없음
  B. 저장 숫자가 원문 발췌에 없음 AND 직전연도 매출 대비 50배 초과 또는 0.1% 미만
플래그만(값 유지): B의 한쪽 조건만 해당, 또는 원문에 수주잔고 숫자가 있는데 NULL.
대상: dart_backlog_quarterly + 같은 (종목, 연도, 분기)의 order_backlog(data_source='dart_backlog').
백업: backlog_fix_backup_20261002, 로그: data_fix_log(run_id), 플래그: backlog_review_flags_20261002.
사용: venv/bin/python scripts/review/fix_backlog_definite_errors_20261002.py [--apply]
"""
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

FX = re.compile(r"단\s*위\s*[:：]?\s*(?:천\s*)?(USD|US\$|달러|EUR|유로|JPY|엔화|CNY|위안|\$)", re.I)
KRW_DECL = re.compile(r"단\s*위\s*[:：][^)]{0,30}(백만원|천원|억원|조원|원)")


def appears(amt, text):
    c = {f"{amt:,.0f}", f"{amt:.0f}"}
    if amt != int(amt):
        c |= {f"{amt:,.1f}", f"{amt:,.2f}"}
    return any(s in text for s in c)


def main(apply):
    conn = connect_primary_db(timeout=600, readonly=not apply)
    rows = conn.execute("""
        SELECT b.stock_code, b.fiscal_year, b.fiscal_quarter, b.backlog_amount, b.backlog_unit, b.backlog_amount_krw, b.source_excerpt,
          (SELECT f.revenue FROM financial_data f WHERE f.stock_code=b.stock_code AND f.year=b.fiscal_year-1 AND f.is_annual AND f.revenue>0
             ORDER BY (f.report_type='CFS') DESC LIMIT 1)
        FROM dart_backlog_quarterly b""").fetchall()
    null_keys, flags = [], []
    for code, y, q, amt, unit, krw, exc, rev in rows:
        e = exc or ""
        if amt is None:
            if re.search(r"수주\s*잔고", e) and re.search(r"[0-9]{1,3}(,[0-9]{3})+", e):
                flags.append((code, y, q, "null_but_excerpt_has_numbers", None))
            continue
        if amt == 0:
            continue
        fx_only = bool(FX.search(e)) and not KRW_DECL.search(e)
        absent = not appears(amt, e)
        ratio = (krw / rev) if (krw and rev) else None
        odd = ratio is not None and (ratio > 50 or ratio < 0.001)
        if fx_only:
            null_keys.append((code, y, q, "foreign_currency_unit_read_as_krw", ratio))
        elif absent and odd:
            null_keys.append((code, y, q, "value_not_in_source_and_ratio_outlier", ratio))
        elif absent or odd:
            flags.append((code, y, q, "value_not_in_source" if absent else "ratio_outlier", ratio))
    print(f"dart_backlog_quarterly {len(rows)}행 → NULL 처리 {len(null_keys)}, 검토 플래그 {len(flags)}")
    from collections import Counter
    print(" NULL 사유:", dict(Counter(k[3] for k in null_keys)), " 플래그 사유:", dict(Counter(f[3] for f in flags)))
    if not apply:
        print("dry-run (적용하려면 --apply)")
        return
    run_id = f"backlog_definite_fix_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    conn.execute("CREATE TABLE IF NOT EXISTS backlog_fix_backup_20261002 (run_id TEXT, table_name TEXT, stock_code TEXT, year INT, quarter INT, reason TEXT, row_json TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS backlog_review_flags_20261002 (stock_code TEXT, year INT, quarter INT, reason TEXT, ratio DOUBLE PRECISION, run_id TEXT)")
    n_b = n_o = 0
    for code, y, q, reason, _ in null_keys:
        conn.execute("""INSERT INTO backlog_fix_backup_20261002 SELECT ?, 'dart_backlog_quarterly', stock_code, fiscal_year, fiscal_quarter, ?, row_to_json(b)::text
                        FROM dart_backlog_quarterly b WHERE stock_code=? AND fiscal_year=? AND fiscal_quarter=?""", (run_id, reason, code, y, q))
        n_b += conn.execute("""UPDATE dart_backlog_quarterly SET backlog_amount=NULL, backlog_amount_krw=NULL, backlog_confidence=0,
                               parser_version=COALESCE(parser_version,'')||'+nulled_20261002', updated_at=CAST(now() AS TEXT)
                               WHERE stock_code=? AND fiscal_year=? AND fiscal_quarter=?""", (code, y, q)).rowcount
        conn.execute("""INSERT INTO backlog_fix_backup_20261002 SELECT ?, 'order_backlog', stock_code, year, quarter, ?, row_to_json(o)::text
                        FROM order_backlog o WHERE stock_code=? AND year=? AND quarter=? AND data_source='dart_backlog'""", (run_id, reason, code, y, q))
        n_o += conn.execute("""UPDATE order_backlog SET backlog_amount=NULL, backlog_normalized=NULL, backlog_to_rev=NULL
                               WHERE stock_code=? AND year=? AND quarter=? AND data_source='dart_backlog'""", (code, y, q)).rowcount
    conn.executemany("INSERT INTO backlog_review_flags_20261002 VALUES (?,?,?,?,?,?)", [f + (run_id,) for f in flags])
    conn.execute("SELECT setval('data_fix_log_id_seq', (SELECT MAX(id) FROM data_fix_log))")
    conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                 (datetime.now().isoformat(timespec="seconds"), "dart_backlog_quarterly+order_backlog",
                  "수주잔고 확실한 오류(외화 단위를 원화로 읽음 / 원문에 없는 값+비율 이상)", n_b + n_o,
                  "A: 외화 단위만 선언된 표, B: 원문에 숫자 없음 AND 매출 대비 >50배 또는 <0.1% → 금액 NULL",
                  "예: 011000 2022Q1 6.8조원(USD 6.83M), 062040 2026Q2 60.9조원(원문 556,788백만원)", "NULL(미확정)",
                  "scripts/review/fix_backlog_definite_errors_20261002.py", run_id))
    conn.commit()
    print(f"적용 완료 run_id={run_id}: dart_backlog_quarterly {n_b}, order_backlog {n_o}, 플래그 {len(flags)}")


if __name__ == "__main__":
    main("--apply" in sys.argv)
