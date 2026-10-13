#!/usr/bin/env python3
"""칸 단위 판단 결과 적용 (2026-10-12). 기본 dry-run.

입력 csv 열: table(financial_data|cash_flow_data), stock_code, year, quarter(0=연간), report_type, field, new_value(빈칸=비움), rule, reason_code(선택), evidence(선택)
  — 실행 AI가 원문을 직접 보고 정한 칸(사용자 10-12 위임). 근거는 rule·evidence에 남긴다.
적용: 백업·fix_log·(reason_code 있으면) financial_field_exception·data_fix_log·잠금 해제(원문 > 잠금)·되읽기·정본 동기화. 출처 '+reviewed_cell_20261012'.
"""
from __future__ import annotations

import argparse
import csv
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
TAG = "+reviewed_cell_20261012"
LOG = {"financial_data": "financial_fix_log", "cash_flow_data": "cashflow_fix_log"}
BASIS = "사용자 10-12 위임 — 실행 AI 칸별 원문 판단"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("csv"); ap.add_argument("--apply", action="store_true")
    ap.add_argument("--insert", action="store_true", help="원장 행이 없는 칸은 새 행으로(같은 회사·연도·분기·종류 칸끼리 한 행)")
    a = ap.parse_args()
    conn = connect_primary_db(timeout=300, readonly=not a.apply)
    plan, missing, regs = [], [], []
    for x in csv.DictReader(open(a.csv)):
        tbl, f = x["table"], x["field"]
        assert tbl in LOG and f.isidentifier()
        q = int(x["quarter"])
        r = conn.execute(f"SELECT id, {f} FROM {tbl} WHERE stock_code=? AND year=? AND report_type=? AND " + ("is_annual" if q == 0 else "quarter=? AND NOT is_annual"),
                         (x["stock_code"], int(x["year"]), x["report_type"]) + (() if q == 0 else (q,))).fetchall()
        if not r:
            missing.append(x); continue
        new = float(x["new_value"]) if x["new_value"].strip() else None
        for rid, old in r:  # 같은 칸 중복 행은 모두
            if not ((old is None) != (new is None) or (old is not None and abs(old - new) > 0.5)):
                if x.get("reason_code"):  # 값은 그대로, 사유만 등록(DB = 원문, FnGuide만 다름 등)
                    regs.append((x["stock_code"], int(x["year"]), q, x["report_type"], f, old, x["rule"], x["reason_code"], x.get("evidence") or ""))
                continue
            plan.append((tbl, rid, x["stock_code"], int(x["year"]), q, x["report_type"], f, old, new, x["rule"], x.get("reason_code") or "", x.get("evidence") or ""))
    regs = sorted(set(regs))
    if a.apply and a.insert and missing:
        now0 = datetime.now().isoformat(timespec="seconds")
        grp = {}
        for x in missing:
            if not x["new_value"].strip():
                continue
            grp.setdefault((x["table"], x["stock_code"], int(x["year"]), int(x["quarter"]), x["report_type"]), []).append(x)
        for (tbl, code, y, q, fs), xs in grp.items():
            conn.execute(f"SELECT setval('{tbl}_id_seq', (SELECT COALESCE(MAX(id),1) FROM {tbl}))")
            cols = list(dict.fromkeys(x["field"] for x in xs))
            vals = {x["field"]: float(x["new_value"]) for x in xs}
            allc = ["stock_code", "year", "quarter", "is_annual", "report_type", "data_source", "created_at", "updated_at"] + cols
            rid = conn.execute(f"INSERT INTO {tbl} ({','.join(allc)}) VALUES ({','.join('?' * len(allc))}) RETURNING id",
                               [code, y, q, q == 0, fs, "reviewed_cell_20261012_insert", now0, now0] + [vals[c] for c in cols]).fetchone()[0]
            for x in xs:
                plan.append((tbl, rid, code, y, q, fs, x["field"], None, vals[x["field"]], x["rule"] + " (새 행)", x.get("reason_code") or "", x.get("evidence") or ""))
        missing = [x for x in missing if not x["new_value"].strip()]
    res = {"at": datetime.now().isoformat(timespec="seconds"), "apply": a.apply, "plan_rows": len(plan), "register_only": len(regs), "missing": missing, "plan": [p[2:10] for p in plan]}
    if a.apply and regs and not plan:
        run_id = f"reviewed_cell_{datetime.now():%Y%m%d_%H%M%S}"
        now = datetime.now().isoformat(timespec="seconds")
        _register(conn, regs, run_id, now)
        conn.commit(); res["run_id"] = run_id
    if a.apply and plan:
        run_id = f"reviewed_cell_{datetime.now():%Y%m%d_%H%M%S}"
        now = datetime.now().isoformat(timespec="seconds")
        for tbl, log in LOG.items():
            ps = [p for p in plan if p[0] == tbl]
            if not ps:
                continue
            bk = f"{tbl}_backup_reviewed_cell_20261012"
            conn.execute(f"CREATE TABLE IF NOT EXISTS {bk} AS SELECT *, CAST(NULL AS TEXT) run_id FROM {tbl} WHERE false")
            ids = sorted({p[1] for p in ps})
            conn.execute(f"INSERT INTO {bk} SELECT *, ? FROM {tbl} WHERE id IN ({','.join('?' * len(ids))})", [run_id] + ids)
            conn.execute(f"UPDATE {tbl} SET data_source=COALESCE(data_source,'')||? WHERE id IN ({','.join('?' * len(ids))}) AND COALESCE(data_source,'') NOT LIKE ?", [TAG] + ids + [f"%{TAG}%"])
            for _, rid, code, y, q, fs, f, old, new, rule, rc, ev in ps:
                conn.execute(f"UPDATE {tbl} SET {f}=?, updated_at=? WHERE id=?", (new, now, rid))
            conn.execute(f"SELECT setval('{log}_id_seq', (SELECT COALESCE(MAX(id),1) FROM {log}))")
            conn.executemany(f"""INSERT INTO {log}(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             [(now, rid, code, y, q, 1 if q == 0 else 0, fs, f, old, new, rule, "DART 원문 칸별 판단(실행 AI)", run_id) for _, rid, code, y, q, fs, f, old, new, rule, rc, ev in ps])
            for code_, y_ in sorted({(p[2], p[3]) for p in ps}):
                conn.execute("UPDATE data_lock SET is_locked=0, unlock_reason=?, unlocked_at=? WHERE stock_code=? AND year=? AND table_name=? AND is_locked=1",
                             (f"원문 > 잠금(§5-10) — {run_id}", now, code_, y_, tbl))
        for _, rid, code, y, q, fs, f, old, new, rule, rc, ev in plan:
            # 값이 바뀐 칸의 기존 사유는 낡은 것 — 새 사유로 교체(칸당 사유 하나). 사유 없이 바뀐 칸은 지배 기준(TOTAL_BASIS)만 남기고 지움
            if rc:
                conn.execute("DELETE FROM financial_field_exception WHERE stock_code=? AND year=? AND quarter=? AND report_type=? AND field=?", (code, y, q, fs, f.removesuffix("_q")))
            else:
                conn.execute("DELETE FROM financial_field_exception WHERE stock_code=? AND year=? AND quarter=? AND report_type=? AND field=? AND reason_code<>'FNGUIDE_TOTAL_BASIS'", (code, y, q, fs, f.removesuffix("_q")))
            if rc:
                conn.execute("""INSERT INTO financial_field_exception(stock_code,year,quarter,report_type,field,reason_code,dart_evidence,db_value,fnguide_value,naver_value,exclusion_reason,approved_basis,run_id,created_at)
                                VALUES (?,?,?,?,?,?,?,?,NULL,NULL,?,?,?,?)""", (code, y, q, fs, f.removesuffix("_q"), rc, ev, old, rule, BASIS, run_id, now))
        _register(conn, regs, run_id, now)
        conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
        conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)""",
                     (now, "financial_data+cash_flow_data", f"칸별 판단 적용({Path(a.csv).name})", len(plan), BASIS, "", "", "scripts/review/apply_reviewed_cells_20261012.py", run_id))
        conn.commit()
        bad = [p for p in plan if (lambda cur: (cur is None) != (p[8] is None) or (cur is not None and abs(float(cur) - p[8]) > 0.5))(
            conn.execute(f"SELECT {p[6]} FROM {p[0]} WHERE id=?", (p[1],)).fetchone()[0])]
        res.update({"run_id": run_id, "post_readback": {"planned": len(plan), "mismatch": len(bad), "pass": not bad},
                    "canonical_sync": {k: v for k, v in _ofs.canonical_sync(conn, run_id, now, {p[1] for p in plan if p[0] == "financial_data"},
                                                                            {p[1] for p in plan if p[0] == "cash_flow_data"}).items() if k != "mismatch_sample"}})
    print(json.dumps(res, ensure_ascii=False, default=str))


def _register(conn, regs, run_id, now):
    for code, y, q, fs, f, dbv, rule, rc, ev in regs:
        conn.execute("DELETE FROM financial_field_exception WHERE stock_code=? AND year=? AND quarter=? AND report_type=? AND field=?", (code, y, q, fs, f.removesuffix("_q")))
        conn.execute("""INSERT INTO financial_field_exception(stock_code,year,quarter,report_type,field,reason_code,dart_evidence,db_value,fnguide_value,naver_value,exclusion_reason,approved_basis,run_id,created_at)
                        VALUES (?,?,?,?,?,?,?,?,NULL,NULL,?,?,?,?)""", (code, y, q, fs, f.removesuffix("_q"), rc, ev, dbv, rule, BASIS, run_id, now))


if __name__ == "__main__":
    main()
