#!/usr/bin/env python3
"""감가상각 정의 구체화(10-13) 적용분 되돌리기 — 기본 dry-run. 사용자가 '되돌림'을 결정했을 때만 --apply.

대상 실행(cash_flow_data.depreciation 변경): reviewed_cell_20261013_225714 · _230141 · _231336 · _231800
  (충돌 판정 1차 → 정의 구체화 전 단일 행 정렬 → 합 기준 재정렬 → 본문 표 1,346칸). 구성요소 표 dep_cf_total: dep_comp_align_20261013_230201 · _231340 · _231820.
복원 원본: cashflow_fix_log 의 같은 행·칸 중 가장 이른 old_value (= 이 작업 전 값), 구성요소는 financial_dep_capex_components_backup_dep_align_20261013 의 가장 이른 백업 행.
복원 후 정본(canonical_cashflow_data)은 공용 canonical_sync로 맞춘다. 로그: cashflow_fix_log(fix_rule='되돌림')·data_fix_log.
주의: 이 스크립트는 depreciation 칸과 구성요소 dep_cf_total·dep_source만 건드린다. 되돌리기 전 값은 정의 미확인(legacy·FnGuide 수집값 등) 상태다.
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

_spec = importlib.util.spec_from_file_location("apply_ofs_extra", ROOT / "scripts" / "review" / "apply_ofs_extra_20261009.py")
_ofs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ofs)
RUNS = ("reviewed_cell_20261013_225714", "reviewed_cell_20261013_230141", "reviewed_cell_20261013_231336", "reviewed_cell_20261013_231800")
COMP_BK = "financial_dep_capex_components_backup_dep_align_20261013"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); a = ap.parse_args()
    conn = connect_primary_db(timeout=900, readonly=not a.apply)
    ph = ",".join("?" * len(RUNS))
    first = {}
    for rid, old, at in conn.execute(f"SELECT row_id, old_value, fixed_at FROM cashflow_fix_log WHERE field_name='depreciation' AND run_id IN ({ph}) ORDER BY fixed_at, id", RUNS).fetchall():
        first.setdefault(rid, old)  # 가장 이른 old_value
    cur = {r[0]: r[1] for i in range(0, len(first), 1000)
           for r in conn.execute(f"SELECT id, depreciation FROM cash_flow_data WHERE id IN ({','.join('?' * len(list(first)[i:i + 1000]))})", list(first)[i:i + 1000]).fetchall()}
    todo = {rid: (cur.get(rid), old) for rid, old in first.items()
            if rid in cur and ((cur[rid] is None) != (old is None) or (old is not None and abs(float(cur[rid]) - float(old)) > 0.5))}
    comp = {}
    for r in conn.execute(f"SELECT stock_code, year, quarter, report_type, dep_cf_total, dep_source, bk_run_id FROM {COMP_BK} ORDER BY bk_run_id").fetchall():
        comp.setdefault((r[0], r[1], r[2], r[3]), (r[4], r[5]))
    res = {"at": datetime.now().isoformat(timespec="seconds"), "apply": a.apply, "ledger_rows_logged": len(first), "ledger_rows_to_restore": len(todo), "component_rows_to_restore": len(comp)}
    if a.apply:
        run_id = f"dep_revert_{datetime.now():%Y%m%d_%H%M%S}"
        now = datetime.now().isoformat(timespec="seconds")
        meta = {r[0]: r[1:] for r in conn.execute(f"SELECT id, stock_code, year, quarter, is_annual, report_type FROM cash_flow_data WHERE id IN ({','.join('?' * len(todo))})", list(todo)).fetchall()} if todo else {}
        conn.execute("SELECT setval('cashflow_fix_log_id_seq', (SELECT COALESCE(MAX(id),1) FROM cashflow_fix_log))")
        for rid, (now_v, old_v) in todo.items():
            conn.execute("UPDATE cash_flow_data SET depreciation=?, updated_at=?, data_source=COALESCE(data_source,'')||'+dep_revert_20261013' WHERE id=?", (old_v, now, rid))
            sc, y, q, ia, rt = meta[rid]
            conn.execute("""INSERT INTO cashflow_fix_log(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (now, rid, sc, y, q, 1 if ia else 0, rt, "depreciation", now_v, old_v, "되돌림 — 감가상각 정의 구체화(10-13) 적용 전 값", "revert_depreciation_definition_20261013.py", run_id))
        for (sc, y, q, rt), (v, src) in comp.items():
            conn.execute("UPDATE financial_dep_capex_components SET dep_cf_total=?, dep_source=?, run_id=?, updated_at=? WHERE stock_code=? AND year=? AND quarter=? AND report_type=?", (v, src, run_id, now, sc, y, q, rt))
        conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
        conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)""",
                     (now, "cash_flow_data+financial_dep_capex_components", "감가상각 정의 구체화 적용분 되돌림", len(todo), "사용자 결정 — 되돌림", "정의 구체화 값", "적용 전 값",
                      "scripts/review/revert_depreciation_definition_20261013.py", run_id))
        conn.commit()
        sync = _ofs.canonical_sync(conn, run_id, now, set(), set(todo))
        res.update({"run_id": run_id, "canonical_sync": {k: v for k, v in sync.items() if k != "refused_sample"}})
    print(json.dumps(res, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
