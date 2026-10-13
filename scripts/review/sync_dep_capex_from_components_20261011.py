#!/usr/bin/env python3
"""감가상각·CapEx 운영 칸 ← 구성요소 표(financial_dep_capex_components) 동기화 (2026-10-11, 3순위). 기본 dry-run.

정본 FINANCIAL §9 메모: 구성요소 표 dep_cf_total은 FnGuide와 맞는데 운영 cash_flow_data.depreciation_q 등이 옛 값(stale)으로 남음.
정의(FINANCIAL §2 표, 2026-10-03 승인): depreciation = 현금흐름표 조정 감가상각(dep_cf_total) / depreciation_amortization = 그 값 + 무형자산 상각 /
  capex = 유형자산 취득(capex_ppe), FnGuide가 무형 포함이면 capex_ppe + capex_intangible(칸마다 FnGuide와 맞는 쪽).
반영: 미확정 칸 중 구성요소 후보가 FnGuide와 200만원 이내일 때만(원칙 0). 부호는 원장 관례 유지(기존 값 부호, 없으면 양수).
적용: 백업·fix_log·readback·정본·재측정.
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

_spec = importlib.util.spec_from_file_location("apply_ofs_extra", ROOT / "scripts" / "review" / "apply_ofs_extra_20261009.py")
_ofs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ofs)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); a = ap.parse_args()
    conn = connect_primary_db(timeout=900, readonly=not a.apply)
    comp = {tuple(r[:4]): r[4:] for r in conn.execute(
        "SELECT stock_code, year, quarter, report_type, dep_cf_total, amort_intangible, capex_ppe, capex_intangible FROM financial_dep_capex_components").fetchall()}
    cells = conn.execute("""SELECT stock_code, year, quarter, report_type, field, db_value, fnguide_value FROM financial_field_verification
        WHERE field IN ('depreciation','capex','depreciation_amortization') AND status NOT LIKE '확정%%' AND fnguide_value IS NOT NULL""").fetchall()
    st, plan = collections.Counter(), []
    for code, y, q, fs, f, dv, fg in cells:
        v = comp.get((code, y, q, fs))
        if not v:
            st["구성요소 없음"] += 1; continue
        dep, am, cp, ci = v
        cands = {"depreciation": [("현금흐름 조정 감가상각", dep)],
                 "depreciation_amortization": [("조정 감가상각 + 무형 상각", None if dep is None else dep + (am or 0))],
                 "capex": [("유형자산 취득", cp), ("유형 + 무형 취득", None if cp is None else cp + (ci or 0))]}[f]
        hit = [(lab, x) for lab, x in cands if x is not None and abs(abs(x) - abs(fg)) <= 2e6]
        if not hit:
            st["구성요소 ≠ FnGuide 또는 값 없음"] += 1; continue
        lab, x = hit[0]
        if f == "depreciation_amortization":
            tbl, col = "financial_data", "depreciation_amortization"
        else:
            tbl, col = "cash_flow_data", (f if q == 0 else f + "_q")
        cond = "is_annual" if q == 0 else "NOT is_annual AND quarter=?"
        rows = conn.execute(f"SELECT id, {col} FROM {tbl} WHERE stock_code=? AND year=? AND report_type=? AND {cond}",
                            (code, y, fs) if q == 0 else (code, y, fs, q)).fetchall()
        if not rows:
            st["원장 행 없음"] += 1; continue
        n = 0
        for rid, old in rows:
            new = abs(x) * (-1 if (old is not None and old < 0) else 1)
            if old is not None and abs(old - new) <= 0.5:
                continue
            plan.append((tbl, rid, code, y, q, fs, col, old, new, f"{lab} = FnGuide(구성요소 표 동기화)")); n += 1
        st["동기화" if n else "이미 같음"] += 1
    res = {"at": datetime.now().isoformat(timespec="seconds"), "apply": a.apply, "stats": dict(st), "plan_rows": len(plan)}
    if a.apply and plan:
        run_id = f"dep_capex_sync_{datetime.now():%Y%m%d_%H%M%S}"
        now = datetime.now().isoformat(timespec="seconds")
        for tbl, log in (("financial_data", "financial_fix_log"), ("cash_flow_data", "cashflow_fix_log")):
            ps = [p for p in plan if p[0] == tbl]
            if not ps:
                continue
            bk = f"{tbl}_backup_dep_capex_sync_20261011"
            conn.execute(f"CREATE TABLE IF NOT EXISTS {bk} AS SELECT *, CAST(NULL AS TEXT) run_id FROM {tbl} WHERE false")
            ids = sorted({p[1] for p in ps})
            for i in range(0, len(ids), 1000):
                part = ids[i:i + 1000]
                conn.execute(f"INSERT INTO {bk} SELECT *, ? FROM {tbl} WHERE id IN ({','.join('?' * len(part))})", [run_id] + part)
            for _, rid, code, y, q, fs, col, old, new, lab in ps:
                conn.execute(f"UPDATE {tbl} SET {col}=?, updated_at=? WHERE id=?", (new, now, rid))
            conn.execute(f"SELECT setval('{log}_id_seq', (SELECT COALESCE(MAX(id),1) FROM {log}))")
            conn.executemany(f"""INSERT INTO {log}(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             [(now, rid, code, y, q, 1 if q == 0 else 0, fs, col, old, new, lab, "financial_dep_capex_components(XBRL 주석·현금흐름표)", run_id)
                              for _, rid, code, y, q, fs, col, old, new, lab in ps])
        conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
        conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)""",
                     (now, "financial_data+cash_flow_data", "감가상각·CapEx 운영 칸 ← 구성요소 표(FnGuide 일치분)", len(plan), "FINANCIAL §2 정의, 원칙 0",
                      json.dumps(dict(st), ensure_ascii=False), "", "scripts/review/sync_dep_capex_from_components_20261011.py", run_id))
        conn.commit()
        bad = [p for p in plan if abs(float(conn.execute(f"SELECT {p[6]} FROM {p[0]} WHERE id=?", (p[1],)).fetchone()[0]) - p[8]) > 0.5]
        res.update({"run_id": run_id, "post_readback": {"planned": len(plan), "mismatch": len(bad), "pass": not bad},
                    "canonical_sync": {k: v for k, v in _ofs.canonical_sync(conn, run_id, now, {p[1] for p in plan if p[0] == "financial_data"},
                                                                            {p[1] for p in plan if p[0] == "cash_flow_data"}).items() if "sample" not in k}})
        conn.close()
        pr = subprocess.run([sys.executable, str(ROOT / "scripts" / "review" / "build_field_verification_20261003.py")], capture_output=True, text=True, cwd=str(ROOT))
        res["field_verification_after"] = (pr.stdout or pr.stderr).strip().splitlines()[-1:]
    print(json.dumps(res, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
