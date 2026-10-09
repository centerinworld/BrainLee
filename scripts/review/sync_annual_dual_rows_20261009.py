#!/usr/bin/env python3
"""PLAN_99_99_NUMERIC P13 — 원장 연간 행 두 벌(quarter 0·4, is_annual) 값 맞추기 (2026-10-09). 기본 dry-run.

배경: `financial_data`·`cash_flow_data`의 같은 연간 기간에 quarter 0 행과 quarter 4 행이 함께 있고(손익·재무상태 9,639쌍·현금흐름 19,036쌍),
칸마다 값이 다른 경우가 많다(대부분 억원 반올림 vs 원 단위, 일부 실질 차이). 화면·전략·백테스트 쿼리 139곳(46파일)이 정렬 없이 어느 한 행을
읽으므로, 읽는 쪽을 고치는 대신 **두 벌의 값을 같게** 만든다.
칸별 규칙(값이 다를 때만):
  1) FnGuide 값(현행 검증의 같은 구분·연간 칸)이 있고 한쪽만 200만원 이내 일치 → 그 값으로 맞춤(근거: FnGuide)
  2) 두 값이 반올림 범위(|차| ≤ max(1억, 0.5%)) 안이고 한쪽만 억원 배수 → 억원 배수가 아닌 값(원 단위 원문)으로(근거: 정밀도 — 의미 같음)
  3) 한쪽이 NULL → 다른 쪽 값이 FnGuide와 200만원 이내 일치할 때만 채움(일부러 비운 칸 보호 — 10-02 감가상각 NULL 처리)
  4) 그 밖(실질 차이·근거 없음) → **보류**(바꾸지 않음, 목록)
제외: 외화·비12월·단위 오류 종목, data_lock, 재작성값·원화 환산 보호 칸.
적용: 백업(`*_backup_p13_20261009`)·financial_fix_log/cashflow_fix_log·data_fix_log·readback·정본 동기화(대표 행)·재측정.
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

SPECS = {
    "financial_data": ("financial_fix_log", ("revenue", "operating_profit", "net_income", "total_assets", "total_liabilities", "total_equity")),
    "cash_flow_data": ("cashflow_fix_log", ("operating_cf", "investing_cf", "financing_cf", "capex", "depreciation", "cash_end")),
}
TOL = 2e6


def is_eok(v):
    return v is not None and v != 0 and abs(v) % 1e8 < 0.5


def decide(va, vb, fg):
    """→ (선택값, 규칙) 또는 (None, 보류사유)"""
    if va is None or vb is None:
        # 10-09: NULL은 일부러 비운 칸일 수 있다(10-02 FnGuide 부풀린 연간 감가상각 → NULL 처리) → FnGuide가 그 값을 확인할 때만 채움
        v = vb if va is None else va
        if fg is None or abs(v - fg) > TOL:
            return None, "보류: 한쪽 NULL·FnGuide 확인 없음(일부러 비운 칸일 수 있음)"
        return v, "NULL 칸 = 같은 기간 다른 행 값(FnGuide 확인)"
    if fg is not None:
        ha, hb = abs(va - fg) <= TOL, abs(vb - fg) <= TOL
        if ha != hb:
            return (va if ha else vb), "FnGuide 일치 값"
    near = abs(va - vb) <= max(1e8, abs(vb) * 0.005)
    if near and is_eok(va) != is_eok(vb):
        return (vb if is_eok(va) else va), "정밀도: 억원 반올림 대신 원 단위 값(같은 값의 반올림 차이)"
    return None, "보류: 실질 차이·근거 없음"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    conn = connect_primary_db(timeout=1800, readonly=not a.apply)
    excl = {r[0] for r in conn.execute("""SELECT stock_code FROM stock_collection_config WHERE config_key IN
        ('fs_quirk:reporting_currency','fs_quirk:unit_scale_fixed','fs_quirk:dart_unit_error')
        OR (config_key='fs_quirk:fiscal_month' AND config_value<>'12')""").fetchall()}
    fg = {(r[0], r[1], r[2], r[3]): float(r[4]) for r in conn.execute(
        "SELECT stock_code, year, report_type, field, fnguide_value FROM financial_field_verification WHERE quarter=0 AND fnguide_value IS NOT NULL").fetchall()}
    plan, held, st = collections.defaultdict(list), [], collections.Counter()
    for tbl, (log, cols) in SPECS.items():
        locks = {(r[0], r[1]) for r in conn.execute("SELECT stock_code, year FROM data_lock WHERE is_locked=1 AND table_name=?", (tbl,)).fetchall()}
        protected = {(r[0], r[1]) for r in conn.execute(
            f"SELECT row_id, field_name FROM {log} WHERE run_id LIKE 'restated_annual_%%' OR run_id LIKE 'fx_krw_%%'").fetchall()}
        sel = ",".join(f"a.{c}, b.{c}" for c in cols)
        for r in conn.execute(f"""SELECT a.id, b.id, a.stock_code, a.year, a.report_type, {sel} FROM {tbl} a JOIN {tbl} b
                                  ON a.stock_code=b.stock_code AND a.year=b.year AND a.report_type=b.report_type
                                  AND a.is_annual AND b.is_annual AND a.quarter=0 AND b.quarter=4""").fetchall():
            r = tuple(r)
            ida, idb, code, y, fs = r[:5]
            for j, col in enumerate(cols):
                va, vb = r[5 + 2 * j], r[6 + 2 * j]
                if va is None and vb is None:
                    continue
                if va is not None and vb is not None and abs(float(va) - float(vb)) <= 0.5:
                    continue
                st[(tbl, "다름")] += 1
                if code in excl or (code, y) in locks:
                    st[(tbl, "제외(외화·비12월·단위·잠금)")] += 1
                    continue
                v, rule = decide(None if va is None else float(va), None if vb is None else float(vb), fg.get((code, y, fs, col)))
                if v is None:
                    st[(tbl, rule)] += 1
                    held.append((tbl, code, y, fs, col, va, vb, fg.get((code, y, fs, col)), rule))
                    continue
                st[(tbl, rule)] += 1
                for rid, old in ((ida, va), (idb, vb)):
                    if old is not None and abs(float(old) - v) <= 0.5:
                        continue
                    if (rid, col) in protected:
                        st[(tbl, "보호 칸 제외")] += 1
                        continue
                    plan[tbl].append((rid, code, y, 0 if rid == ida else 4, fs, col, old, v, f"P13 두 벌 맞춤: {rule}"))
    res = {"at": datetime.now().isoformat(timespec="seconds"), "mode": "apply" if a.apply else "dry_run",
           "stats": {f"{k[0]}|{k[1]}": n for k, n in st.items()}, "updates": {k: len(v) for k, v in plan.items()},
           "held": len(held), "held_sample": held[:60], "update_sample": {k: v[:20] for k, v in plan.items()}}
    if a.apply and any(plan.values()):
        run_id = f"p13_dual_sync_{datetime.now():%Y%m%d_%H%M%S}"
        now = datetime.now().isoformat(timespec="seconds")
        bad = []
        for tbl, ups in plan.items():
            log = SPECS[tbl][0]
            bk = f"{tbl}_backup_p13_20261009"
            conn.execute(f"CREATE TABLE IF NOT EXISTS {bk} AS SELECT *, CAST(NULL AS TEXT) run_id FROM {tbl} WHERE false")
            ids = sorted({u[0] for u in ups})
            for i in range(0, len(ids), 1000):
                part = ids[i:i + 1000]
                conn.execute(f"INSERT INTO {bk} SELECT *, ? FROM {tbl} WHERE id IN ({','.join('?' * len(part))})", [run_id] + part)
            for rid, code, y, q, fs, col, old, new, rule in ups:
                conn.execute(f"UPDATE {tbl} SET {col}=?, updated_at=? WHERE id=?", (new, now, rid))
            conn.execute(f"SELECT setval('{log}_id_seq', (SELECT COALESCE(MAX(id),1) FROM {log}))")
            conn.executemany(f"""INSERT INTO {log}(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             [(now, rid, code, y, q, 1, fs, col, old, new, rule, "같은 기간 다른 연간 행·FnGuide", run_id) for rid, code, y, q, fs, col, old, new, rule in ups])
        conn.execute("SELECT setval('data_fix_log_id_seq', (SELECT MAX(id) FROM data_fix_log))")
        conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                     (now, "financial_data+cash_flow_data", "연간 행 두 벌(quarter 0·4) 값 맞춤", sum(len(v) for v in plan.values()),
                      "FnGuide 일치 값 / 반올림 범위면 원 단위 값 / NULL 채움, 실질 차이는 보류",
                      json.dumps(res["stats"], ensure_ascii=False)[:900], json.dumps(res["updates"]), "scripts/review/sync_annual_dual_rows_20261009.py", run_id))
        conn.commit()
        for tbl, ups in plan.items():
            for rid, code, y, q, fs, col, old, new, rule in ups:
                v = conn.execute(f"SELECT {col} FROM {tbl} WHERE id=?", (rid,)).fetchone()[0]
                if v is None or abs(float(v) - new) > 0.5:
                    bad.append((tbl, rid, col))
        res["post_readback"] = {"planned": sum(len(v) for v in plan.values()), "mismatch": len(bad), "pass": not bad}
        res["canonical_sync"] = _ofs.canonical_sync(conn, run_id, now, {u[0] for u in plan.get("financial_data", [])},
                                                    {u[0] for u in plan.get("cash_flow_data", [])})
        res["applied"], res["run_id"] = True, run_id
        conn.close()
        pr = subprocess.run([sys.executable, str(ROOT / "scripts" / "review" / "build_field_verification_20261003.py")],
                            capture_output=True, text=True, cwd=str(ROOT))
        res["field_verification_after"] = (pr.stdout or pr.stderr).strip().splitlines()[-1:]
        print("readback", res["post_readback"], "정본", {k: v for k, v in res["canonical_sync"].items() if not k.endswith("sample")},
              "재측정", res["field_verification_after"], flush=True)
    out = ROOT / "research_outputs" / "financial_rereview_20261002" / f"p13_dual_sync_20261009_{'apply' if a.apply else 'dry'}.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print(json.dumps({k: res[k] for k in ("mode", "stats", "updates", "held")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
