#!/usr/bin/env python3
"""재작성 칸 단위 오기 바로잡기 (2026-10-12). 기본 dry-run.

원인: 다음 해 사업보고서 전기(frmtrm) 칸이 원래 공시의 정확히 1,000배·1,000,000배로 적힌 종목(032680, 311390 2020~2021)이
  §2-5 재작성 우선 규칙으로 DB에 들어갔다. 오전의 규모 검사(10 × 총자산 중앙값)는 총자산 자체가 부풀어 있어 못 걸렀다(FINANCIAL §5 실패 29 보강).
판정: 2016~2022 DB 값 ÷ 같은 칸 원래 공시 원문 = 1,000 또는 1,000,000(±0.5%) → 원래 공시 원문으로.
  그 회사-연도-종류의 손익 4분기는 원래 공시 기준(원래 연간 − 원래 3분기 누적)으로, 재무상태 4분기 = 연간으로 다시 맞춘다.
  현금흐름(cash_flow_data 누적·3개월)도 같은 배수 검사.
적용: 백업·fix_log·financial_field_exception(RESTATED_UNIT_ERROR)·data_fix_log·되읽기·정본 동기화. 출처 '+restated_unit_fix_20261012'.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
import fin_norm as fn  # noqa: E402

_spec = importlib.util.spec_from_file_location("apply_ofs_extra", ROOT / "scripts" / "review" / "apply_ofs_extra_20261009.py")
_ofs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ofs)
TAG = "+restated_unit_fix_20261012"
FD = ("revenue", "operating_profit", "net_income", "total_assets", "total_liabilities", "total_equity")
CF = ("operating_cf", "investing_cf", "financing_cf")
BASIS = "사용자 10-12 위임 — 단위 오기는 정상 단위 쪽 원문(⑦)"


def _mult(v, o):
    if v is None or o in (None, 0) or abs(v) < 1e9:
        return None
    k = v / o
    return next((m for m in (1e3, 1e6) if abs(k / m - 1) < 0.005), None)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    conn = connect_primary_db(timeout=600, readonly=not a.apply)
    plan, exc, groups = [], [], set()  # plan: (tbl, rid, code, y, q, fs, fld, old, new, rule)
    for r in conn.execute(f"SELECT id, stock_code, year, quarter, is_annual, report_type, {','.join(FD)} FROM financial_data WHERE year BETWEEN 2016 AND 2022").fetchall():
        rid, code, y, q, ia, fs = r[:6]
        rf = fn.effective_fs(code, y, fs)
        for f, v in zip(FD, r[6:]):
            if v is None or abs(v) < 1e9:
                continue
            qq = 0 if ia else q
            if qq == 4 and not f.startswith("total"):
                continue
            o = fn.raw(code, y, 0 if qq in (0, 4) else qq, rf, f)
            m = _mult(v, o)
            if not m and qq in (0, 4):  # 반대 방향: 그해 원문이 1,000배이고 다음 해 재작성 칸이 정상(311390·065150 2022)
                o = fn.latest_annual(code, y, rf, f)[0]
                m = _mult(v, o)
            if m:
                plan.append(("financial_data", rid, code, y, qq, fs, f, v, o, f"원문 한쪽 {m:,.0f}배 단위 오기 → 정상 단위 원문"))
                groups.add((code, y, fs))
                exc.append((code, y, qq, fs, f, "RESTATED_UNIT_ERROR", f"DB {v:,.0f} = 다른 원문 칸 {o:,.0f} × {m:,.0f}", v, "원문 한쪽(그해 당기 또는 다음 해 전기 칸)이 단위 오기 — 정상 단위 값 사용"))
    for r in conn.execute(f"SELECT id, stock_code, year, quarter, is_annual, report_type, {','.join(CF)} FROM cash_flow_data WHERE year BETWEEN 2016 AND 2022 AND quarter IS NOT NULL").fetchall():
        rid, code, y, q, ia, fs = r[:6]
        rf = fn.effective_fs(code, y, fs)
        for f, v in zip(CF, r[6:]):
            o = fn.raw(code, y, 0 if (ia or q == 4) else q, rf, f)
            m = _mult(v, o)
            if not m and (ia or q == 4):
                o = fn.latest_annual(code, y, rf, f)[0]
                m = _mult(v, o)
            if m:
                plan.append(("cash_flow_data", rid, code, y, 0 if ia else q, fs, f, v, o, f"원문 한쪽 {m:,.0f}배 단위 오기 → 정상 단위 원문"))
                exc.append((code, y, 0 if ia else q, fs, f, "RESTATED_UNIT_ERROR", f"DB {v:,.0f} = 다른 원문 칸 {o:,.0f} × {m:,.0f}", v, "원문 한쪽(그해 당기 또는 다음 해 전기 칸)이 단위 오기 — 정상 단위 값 사용"))
    # 같은 회사-연도-종류에서 단위 오기 연간으로 파생 계산된 값(예: 자본 = 1,000배 자산 − 부채)도 원문의 100배 넘게 다르면 원문으로
    groups |= {(p[2], p[3], p[5]) for p in plan} | {tuple(r) for r in conn.execute(
        "SELECT DISTINCT stock_code, year, report_type FROM financial_field_exception WHERE reason_code='RESTATED_UNIT_ERROR'").fetchall()}
    planned = {(p[1], p[6]) for p in plan}
    for code, y, fs in sorted(groups):
        rf = fn.effective_fs(code, y, fs)
        for r in conn.execute(f"SELECT id, quarter, is_annual, {','.join(FD)} FROM financial_data WHERE stock_code=? AND year=? AND report_type=?", (code, y, fs)).fetchall():
            qq = 0 if r[2] else r[1]
            for f, v in zip(FD, r[3:]):
                if (r[0], f) in planned or v is None or (qq == 4 and not f.startswith("total")):
                    continue
                o = fn.raw(code, y, 0 if qq in (0, 4) else qq, rf, f)
                if o and abs(v) > 100 * abs(o):
                    plan.append(("financial_data", r[0], code, y, qq, fs, f, v, o, "단위 오기 값에서 파생된 칸(원문의 100배 초과) → 원래 공시 원문"))
                    exc.append((code, y, qq, fs, f, "RESTATED_UNIT_ERROR", f"DB {v:,.0f} vs 원래 공시 {o:,.0f}", v, "단위 오기 값으로 계산된 칸 — 원래 공시값 사용"))
    # 손익 4분기 = 원래 연간 − 원래 3분기 누적, 재무상태 4분기 = 연간
    fixed = {(p[2], p[3], p[4], p[5], p[6]): p[8] for p in plan if p[0] == "financial_data"}
    for code, y, fs in sorted(groups):
        rf = fn.effective_fs(code, y, fs)
        rows = {(0 if r[1] else r[0]): r for r in conn.execute(f"SELECT quarter, is_annual, id, {','.join(FD)} FROM financial_data WHERE stock_code=? AND year=? AND report_type=? ORDER BY id DESC", (code, y, fs)).fetchall()}
        if 4 not in rows:
            continue
        r4 = dict(zip(FD, rows[4][3:]))
        for f in FD:
            A = fixed.get((code, y, 0, fs, f), (dict(zip(FD, rows[0][3:])) if 0 in rows else {}).get(f))
            if f.startswith("total"):
                new = A
            else:
                y3 = fn.ytd3(code, y, rf, f)
                if y3 is None and all(q_ in rows and rows[q_][3 + FD.index(f)] is not None for q_ in (1, 2, 3)):
                    y3 = sum(rows[q_][3 + FD.index(f)] for q_ in (1, 2, 3))  # 분기 원문 없음 → D-6 대체(연간 − DB 1~3분기 합)
                new = None if A is None or y3 is None else fn.q4_flow(A, y3)
                if f == "revenue" and new is not None and new < -2e6:
                    continue
            if new is not None and r4[f] is not None and abs(r4[f] - new) > 0.5 and not any(p[1] == rows[4][2] and p[6] == f for p in plan):
                plan.append(("financial_data", rows[4][2], code, y, 4, fs, f, r4[f], new, "단위 바로잡은 연간 기준 4분기 다시 계산(D-6)"))
    res = {"at": datetime.now().isoformat(timespec="seconds"), "apply": a.apply, "plan_rows": len(plan), "exceptions": len(exc),
           "plan": [p[2:] for p in plan]}
    if a.apply and plan:
        run_id = f"restated_unit_fix_{datetime.now():%Y%m%d_%H%M%S}"
        now = datetime.now().isoformat(timespec="seconds")
        for tbl, log in (("financial_data", "financial_fix_log"), ("cash_flow_data", "cashflow_fix_log")):
            ps = [p for p in plan if p[0] == tbl]
            if not ps:
                continue
            bk = f"{tbl}_backup_restated_unit_fix_20261012"
            conn.execute(f"CREATE TABLE IF NOT EXISTS {bk} AS SELECT *, CAST(NULL AS TEXT) run_id FROM {tbl} WHERE false")
            ids = sorted({p[1] for p in ps})
            conn.execute(f"INSERT INTO {bk} SELECT *, ? FROM {tbl} WHERE id IN ({','.join('?' * len(ids))})", [run_id] + ids)
            conn.execute(f"UPDATE {tbl} SET data_source=COALESCE(data_source,'')||? WHERE id IN ({','.join('?' * len(ids))}) AND COALESCE(data_source,'') NOT LIKE ?", [TAG] + ids + [f"%{TAG}%"])
            for _, rid, code, y, q, fs, fld, old, new, rule in ps:
                conn.execute(f"UPDATE {tbl} SET {fld}=?, updated_at=? WHERE id=?", (new, now, rid))
            conn.execute(f"SELECT setval('{log}_id_seq', (SELECT COALESCE(MAX(id),1) FROM {log}))")
            conn.executemany(f"""INSERT INTO {log}(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             [(now, rid, code, y, q, 1 if q == 0 else 0, fs, fld, old, new, rule, "DART 원문(data_raw/dart_fnltt)", run_id) for _, rid, code, y, q, fs, fld, old, new, rule in ps])
            for code_, y_ in sorted({(p[2], p[3]) for p in ps}):
                conn.execute("UPDATE data_lock SET is_locked=0, unlock_reason=?, unlocked_at=? WHERE stock_code=? AND year=? AND table_name=? AND is_locked=1",
                             (f"원문 > 잠금(§5-10) — {run_id}", now, code_, y_, tbl))
        for code, y, q, fs, fld, rc, ev, dbv, why in exc:
            conn.execute("DELETE FROM financial_field_exception WHERE stock_code=? AND year=? AND quarter=? AND report_type=? AND field=? AND reason_code=?", (code, y, q, fs, fld, rc))
            conn.execute("""INSERT INTO financial_field_exception(stock_code,year,quarter,report_type,field,reason_code,dart_evidence,db_value,fnguide_value,naver_value,exclusion_reason,approved_basis,run_id,created_at)
                            VALUES (?,?,?,?,?,?,?,?,NULL,NULL,?,?,?,?)""", (code, y, q, fs, fld, rc, ev, dbv, why, BASIS, run_id, now))
        conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
        conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)""",
                     (now, "financial_data+cash_flow_data", "재작성 칸 1,000배 단위 오기 → 원래 공시", len(plan), BASIS, "", "", "scripts/review/fix_restated_unit_error_20261012.py", run_id))
        conn.commit()
        bad = [p for p in plan if abs(float(conn.execute(f"SELECT {p[6]} FROM {p[0]} WHERE id=?", (p[1],)).fetchone()[0]) - p[8]) > 0.5]
        res.update({"run_id": run_id, "post_readback": {"planned": len(plan), "mismatch": len(bad), "pass": not bad},
                    "canonical_sync": {k: v for k, v in _ofs.canonical_sync(conn, run_id, now, {p[1] for p in plan if p[0] == "financial_data"},
                                                                            {p[1] for p in plan if p[0] == "cash_flow_data"}).items() if k != "mismatch_sample"}})
    print(json.dumps(res, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
