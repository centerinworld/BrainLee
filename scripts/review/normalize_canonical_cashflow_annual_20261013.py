#!/usr/bin/env python3
"""canonical_cashflow_data 연간 키 정리 (2026-10-13, 외부 검토 P1). 기본 dry-run.

문제: 연간 정본 행이 quarter=0 21,623행과 quarter=4 19,468행(전부 decision_reason='rebuild_2022_2025')으로 중복(19,122묶음)이고, q4 행은 낡은 값이다
      (canonical_financial_data는 10-08 `repair_canonical_financial_annual_keys`로 정리했고 연간 = quarter 0 한 키). 표준: 연간 정본 = quarter 0.
규칙(정본은 원장의 거울 — 원장 연간 행이 근거):
  1) 연간 묶음마다 원장 연간 행(quarter 0·4·NULL)을 모아 공용 canonical_sync로 정본 quarter=0 행을 맞춘다(대표행: 정본이 가리키는 행 → quarter 0 → id 작은 순).
  2) **원장** 대표행 칸이 NULL이거나 '억/백만 단위로 반올림된 값'인데 같은 묶음의 다른 원장 연간 행이 같은 값을 더 정확하게(반올림 허용오차 이내, 반올림 안 된 값) 갖고 있으면
     원장 대표행을 채움·정밀화(백업 cash_flow_data_backup_annual_refine_20261013 + cashflow_fix_log)하고 정본에 동기화한다. 정본만 고치면 다음 동기화가 되돌린다(10-13 실측).
  3) 그 뒤 정본 quarter=4 연간 행은 백업(canonical_cashflow_data_backup_annual_norm_20261013) 후 삭제. quarter=0이 없으면 quarter=0으로 옮김(백업 후).
  4) 원장 행 사이에도 반올림 오차를 넘는 값 충돌이 남는 칸은 대표행 값을 유지하고 충돌 목록 CSV(canonical_cf_annual_conflicts_20261013.csv)에 남긴다 — 원장 중복 행 문제이며 이 스크립트가 값을 고르지 않는다.
"""
from __future__ import annotations

import argparse
import collections
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
OUT = ROOT / "research_outputs" / "financial_rereview_20261002"
CUM = ("operating_cf", "investing_cf", "financing_cf", "capex", "cash_end", "depreciation")
BK = "canonical_cashflow_data_backup_annual_norm_20261013"


def _rounded(v, g):
    return v != 0 and v % g == 0


def rounding_pair(a: float, b: float):
    """(정밀값, 반올림값) — 둘이 같은 값의 정밀/반올림 표기이면. 아니면 None."""
    for g, tol in ((1e6, 5e5), (1e8, 5e7), (1e9, 5e8)):
        if abs(a - b) <= tol:
            ra, rb = _rounded(a, g), _rounded(b, g)
            if ra != rb:
                return (b, a) if ra else (a, b)
    return None


def refine(rep: dict, others: list) -> tuple:
    """대표행 칸을 다른 원장 행으로 채우기·정밀화. 반환: (갱신 dict, 충돌 list[(칸, 대표값, 다른값)])."""
    upd, conflicts = {}, []
    for f in CUM:
        cur = rep.get(f)
        for o in others:
            ov = o.get(f)
            if ov is None:
                continue
            if cur is None:
                cur = upd[f] = float(ov)
                continue
            if abs(float(ov) - float(cur)) <= 0.5:
                continue
            pair = rounding_pair(float(cur), float(ov))
            if pair and pair[0] == float(ov):   # 대표값이 반올림, 다른 행이 정밀 → 정밀화
                cur = upd[f] = float(ov)
            elif pair:                            # 대표값이 정밀 → 유지
                continue
            else:
                conflicts.append((f, float(cur), float(ov)))
    return upd, conflicts


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); a = ap.parse_args()
    conn = connect_primary_db(timeout=900, readonly=not a.apply)
    keys = [tuple(r) for r in conn.execute("SELECT DISTINCT stock_code, year, report_type FROM canonical_cashflow_data WHERE is_annual=1 AND quarter=4").fetchall()]
    kset = set(keys)
    led = collections.defaultdict(list)
    for r in conn.execute(f"SELECT id, stock_code, year, report_type, quarter, {','.join(CUM)} FROM cash_flow_data WHERE is_annual ORDER BY id").fetchall():
        k = (r[1], r[2], r[3])
        if k in kset:
            led[k].append(dict(zip(("id", "stock_code", "year", "report_type", "quarter") + CUM, r)))
    canon = {}
    for r in conn.execute(f"SELECT stock_code, year, report_type, quarter, source_row_id, {','.join(CUM)} FROM canonical_cashflow_data WHERE is_annual=1 AND quarter IN (0,4)").fetchall():
        canon[(r[0], r[1], r[2], r[3])] = dict(zip(("stock_code", "year", "report_type", "quarter", "source_row_id") + CUM, r))
    n_q0 = sum(1 for k in keys if (*k, 0) in canon)
    # 원장 정밀화 후보 계산 (dry-run·apply 공통) — 10-14 외부 검토 P2: dry-run이 이 수치를 숨겨 --apply가 noop이 아님을 알 수 없었음
    refine_groups = {}
    for r in conn.execute(f"SELECT id, stock_code, year, report_type, quarter, {','.join(CUM)} FROM cash_flow_data WHERE is_annual ORDER BY id").fetchall():
        refine_groups.setdefault((r[1], r[2], r[3]), []).append(dict(zip(("id","stock_code","year","report_type","quarter")+CUM, r)))
    dr_refine_cells = dr_refine_groups = 0
    for k, rows in refine_groups.items():
        if len(rows) < 2:
            continue
        cur = conn.execute("SELECT source_row_id FROM canonical_cashflow_data WHERE stock_code=? AND year=? AND report_type=? AND is_annual=1 AND quarter=0", k).fetchone()
        src = cur[0] if cur else None
        rows.sort(key=lambda r: (r["id"] != src, 99 if r["quarter"] is None else r["quarter"], r["id"]))
        upd, _ = refine(rows[0], rows[1:])
        if upd:
            dr_refine_groups += 1
            dr_refine_cells += len(upd)
    res = {"at": datetime.now().isoformat(timespec="seconds"), "apply": a.apply, "annual_q4_rows": len(keys), "with_q0_row": n_q0, "q4_only": len(keys) - n_q0,
           "keys_without_ledger_annual_row": sum(1 for k in keys if not led.get(k)), "ledger_annual_rows": sum(len(v) for v in led.values()),
           "would_refine_groups": dr_refine_groups, "would_refine_cells": dr_refine_cells}
    if not a.apply:
        print(json.dumps(res, ensure_ascii=False)); return
    run_id = f"canonical_cf_annual_norm_{datetime.now():%Y%m%d_%H%M%S}"
    now = datetime.now().isoformat(timespec="seconds")
    ids = {r["id"] for rows in led.values() for r in rows}
    sync = _ofs.canonical_sync(conn, run_id, now, set(), ids) if ids else {"pass": True, "readback_mismatch": 0, "cf_refused": 0, "cf_rows": 0, "cf_synced": 0}
    res["canonical_sync"] = sync
    conn.execute(f"CREATE TABLE IF NOT EXISTS {BK} AS SELECT *, CAST(NULL AS TEXT) run_id FROM canonical_cashflow_data WHERE false")
    # 2) 원장 대표행 채우기·정밀화 (정본은 원장의 거울 — 원장을 고치고 동기화)
    refined = collections.Counter(); conflicts = []; touched = []
    locked = {(r[0], r[1]) for r in conn.execute("SELECT stock_code, year FROM data_lock WHERE is_locked=1 AND table_name='cash_flow_data'").fetchall()}
    conn.execute("CREATE TABLE IF NOT EXISTS cash_flow_data_backup_annual_refine_20261013 AS SELECT *, CAST(NULL AS TEXT) run_id FROM cash_flow_data WHERE false")
    groups = {}
    for r in conn.execute(f"SELECT id, stock_code, year, report_type, quarter, {','.join(CUM)} FROM cash_flow_data WHERE is_annual ORDER BY id").fetchall():
        groups.setdefault((r[1], r[2], r[3]), []).append(dict(zip(("id", "stock_code", "year", "report_type", "quarter") + CUM, r)))
    for k, rows in groups.items():
        if len(rows) < 2:
            continue
        cur = conn.execute("SELECT source_row_id FROM canonical_cashflow_data WHERE stock_code=? AND year=? AND report_type=? AND is_annual=1 AND quarter=0", k).fetchone()
        src = cur[0] if cur else None
        rows.sort(key=lambda r: (r["id"] != src, 99 if r["quarter"] is None else r["quarter"], r["id"]))
        rep, others = rows[0], rows[1:]
        upd, conf = refine(rep, others)
        conflicts += [(*k, f, rv, ov) for f, rv, ov in conf]
        if not upd:
            continue
        if (k[0], k[1]) in locked:
            res["locked_skipped"] = res.get("locked_skipped", 0) + 1
            continue
        conn.execute(f"INSERT INTO cash_flow_data_backup_annual_refine_20261013 SELECT *, ? FROM cash_flow_data WHERE id=?", (run_id, rep["id"]))
        for f, v in upd.items():
            conn.execute(f"UPDATE cash_flow_data SET {f}=?, updated_at=? WHERE id=?", (v, now, rep["id"]))
            kind = "채움" if rep[f] is None else "정밀화"
            refined[kind] += 1
            conn.execute("SELECT setval('cashflow_fix_log_id_seq', (SELECT COALESCE(MAX(id),1) FROM cashflow_fix_log))")
            conn.execute("""INSERT INTO cashflow_fix_log(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (now, rep["id"], k[0], k[1], rep["quarter"], 1, k[2], f, rep[f], v,
                                                                   f"연간 중복 원장 행 {kind}(같은 묶음 다른 연간 행의 정밀값, 반올림 허용오차 이내)", "원장 연간 중복 행 정리(외부 검토 P1)", run_id))
        conn.execute("UPDATE cash_flow_data SET data_source=COALESCE(data_source,'')||'+annual_refine_20261013' WHERE id=? AND COALESCE(data_source,'') NOT LIKE '%annual_refine_20261013%'", (rep["id"],))
        touched.append(rep["id"])
    if touched:
        res["refine_sync"] = {k: v for k, v in _ofs.canonical_sync(conn, run_id, now, set(), set(touched)).items() if k != "refused_sample"}
    # 3) q4 연간 정본 행 정리
    deleted = moved = 0
    for k in keys:
        has0 = conn.execute("SELECT 1 FROM canonical_cashflow_data WHERE stock_code=? AND year=? AND report_type=? AND is_annual=1 AND quarter=0", k).fetchone()
        conn.execute(f"INSERT INTO {BK} SELECT *, ? FROM canonical_cashflow_data WHERE stock_code=? AND year=? AND report_type=? AND is_annual=1 AND quarter=4", (run_id, *k))
        if has0:
            conn.execute("DELETE FROM canonical_cashflow_data WHERE stock_code=? AND year=? AND report_type=? AND is_annual=1 AND quarter=4", k); deleted += 1
        else:
            conn.execute("UPDATE canonical_cashflow_data SET quarter=0, updated_at=? WHERE stock_code=? AND year=? AND report_type=? AND is_annual=1 AND quarter=4", (now, *k)); moved += 1
    res.update({"refined_cells": dict(refined), "q4_deleted": deleted, "q4_moved_to_q0": moved, "conflict_cells": len(conflicts)})
    with open(OUT / "canonical_cf_annual_conflicts_20261013.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow("stock_code year report_type field representative_value other_ledger_value".split()); w.writerows(conflicts)
    res["annual_q4_after"] = conn.execute("SELECT count(*) FROM canonical_cashflow_data WHERE is_annual=1 AND quarter=4").fetchone()[0]
    res["annual_dup_groups_after"] = conn.execute("SELECT count(*) FROM (SELECT 1 FROM canonical_cashflow_data WHERE is_annual=1 GROUP BY stock_code, year, report_type HAVING count(*)>1) t").fetchone()[0]
    conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
    conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)""",
                 (now, "canonical_cashflow_data", "연간 정본 키 정리(quarter 4 → 0)", len(keys), "원장 연간 행 기준 재동기화 후 q4 정본 행 백업·삭제(외부 검토 P1)",
                  f"연간 q4 {len(keys)}행(q0 있음 {n_q0}, q4만 {len(keys) - n_q0})", f"q4 {res['annual_q4_after']}행, 연간 중복 묶음 {res['annual_dup_groups_after']}, 삭제 {deleted}, 이동 {moved}, 채움/정밀화 {dict(refined)}, 값 충돌 {len(conflicts)}칸({'canonical_cf_annual_conflicts_20261013.csv'})",
                  "scripts/review/normalize_canonical_cashflow_annual_20261013.py", run_id))
    conn.commit()
    res["run_id"] = run_id
    (OUT / f"{run_id}.json").write_text(json.dumps(res, ensure_ascii=False, default=str, indent=1))
    print(json.dumps({k: v for k, v in res.items() if k != "canonical_sync"} | {"sync_pass": sync["pass"], "sync_readback_mismatch": sync["readback_mismatch"],
                                                                                "sync_refused": sync["cf_refused"], "sync_rows": sync["cf_rows"], "sync_synced": sync["cf_synced"]}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
