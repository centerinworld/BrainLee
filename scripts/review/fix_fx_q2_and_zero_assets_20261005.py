#!/usr/bin/env python3
"""외국기업 2026년 2분기 원통화 행 정리 + 자산총계 0 행 NULL 처리(2026-10-05, FINANCIAL_STATEMENTS.md §9-2-8 #5).

1) backfill_dart_q2_financials.py(dart_q2_verified)가 보고통화 종목(fs_quirk:reporting_currency)을 환산 없이 저장 — 1분기 대비 1/1,000 크기.
   같은 분기 FnGuide 원화 값이 없어 환산을 검증할 수 없으므로 백업 후 삭제(틀린 값보다 결측, fail-closed). 950260(외국 신규 상장)은 보고통화 '확인 필요' 표시.
2) total_assets = 0 행(불가능한 값) → NULL(백업·fix_log).
기본 dry-run, --apply.
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    conn = connect_primary_db(timeout=600, readonly=not a.apply)
    fx = {r[0] for r in conn.execute("SELECT stock_code FROM stock_collection_config WHERE config_key='fs_quirk:reporting_currency'").fetchall()} | {"950260"}
    ph = ",".join("?" * len(fx))
    # 원통화 판별: 같은 종목 2026년 1분기 대비 자산(없으면 매출) 크기가 1/20 미만 — 이미 원화로 환산된 13종목(비율 0.9~1.14)은 건드리지 않는다
    raw_codes = set()
    for code in fx:
        q2 = conn.execute("SELECT MAX(total_assets), MAX(revenue) FROM financial_data WHERE stock_code=? AND year=2026 AND quarter=2 AND NOT is_annual AND data_source LIKE 'dart_q2_verified%%'", (code,)).fetchone()
        q1 = conn.execute("SELECT MAX(total_assets), MAX(revenue) FROM financial_data WHERE stock_code=? AND year=2026 AND quarter=1 AND NOT is_annual", (code,)).fetchone()
        if not q2 or not q1:
            continue
        ra = (q2[0] / q1[0]) if q2[0] and q1[0] else None
        rr = (q2[1] / q1[1]) if q2[1] and q1[1] else None
        if (ra is not None and ra < 0.05) or (not q2[0] and rr is not None and rr < 0.05):
            raw_codes.add(code)
    ph = ",".join("?" * len(raw_codes)) or "''"
    dels = {t: [tuple(r) for r in conn.execute(f"SELECT id, stock_code, year, quarter, report_type FROM {t} WHERE stock_code IN ({ph}) AND year=2026 AND quarter=2 AND NOT is_annual AND data_source LIKE 'dart_q2_verified%%'", tuple(raw_codes)).fetchall()]
            for t in ("financial_data", "cash_flow_data")}
    zeros = [tuple(r) for r in conn.execute("SELECT id, stock_code, year, quarter, is_annual, report_type FROM financial_data WHERE total_assets=0").fetchall()]
    zeros = [z for z in zeros if not any(z[0] == d[0] for d in dels["financial_data"])]  # 삭제할 행은 제외
    print({t: len(v) for t, v in dels.items()}, "삭제 대상:", sorted({r[1] for v in dels.values() for r in v}))
    print("자산총계 0 행", zeros)
    if not a.apply:
        return
    run_id = f"fx_q2_zero_fix_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    for t, rows in dels.items():
        conn.execute(f"CREATE TABLE IF NOT EXISTS {t}_backup_unit_20261005 AS SELECT *, CAST(NULL AS TEXT) run_id FROM {t} WHERE false")
        for r in rows:
            conn.execute(f"INSERT INTO {t}_backup_unit_20261005 SELECT *, ? FROM {t} WHERE id=?", (run_id, r[0]))
            conn.execute(f"DELETE FROM {t} WHERE id=?", (r[0],))
    conn.execute("SELECT setval('financial_fix_log_id_seq',(SELECT MAX(id) FROM financial_fix_log))")
    for rid, code, y, q, ann, fs in zeros:
        conn.execute("INSERT INTO financial_data_backup_unit_20261005 SELECT *, ? FROM financial_data WHERE id=?", (run_id, rid))
        conn.execute("UPDATE financial_data SET total_assets=NULL, updated_at=? WHERE id=?", (now, rid))
        conn.execute("""INSERT INTO financial_fix_log(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (now, rid, code, y, q, int(bool(ann)), fs, "total_assets", 0, None, "자산총계 0(불가능한 값) → NULL", "내부 검사", run_id))
    conn.execute("""INSERT INTO stock_collection_config(stock_code, config_key, config_value) VALUES ('950260','fs_quirk:reporting_currency','?(외국 신규 상장, 2026-10-05 확인 필요)')
                    ON CONFLICT (stock_code, config_key) DO NOTHING""")
    conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
    n = sum(len(v) for v in dels.values())
    conn.execute("INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)",
                 (now, "financial_data+cash_flow_data", "외국기업 2026Q2 원통화 행 삭제·자산 0 NULL", n + len(zeros), "보고통화 종목 dart_q2_verified 2026Q2 삭제(환산 검증 불가), total_assets=0 → NULL",
                  "", f"삭제 {n}, NULL {len(zeros)}", "scripts/review/fix_fx_q2_and_zero_assets_20261005.py", run_id))
    conn.commit()
    print("적용 완료", run_id, n, len(zeros))


if __name__ == "__main__":
    main()
