#!/usr/bin/env python3
"""10-12 2021~22 정렬(align_raw_2021_2022_20261012_*)에서 규모상 불가능한 값으로 바뀐 칸 되돌리기. 기본 dry-run.

원인: 다음 해 사업보고서 전기 칸(재작성) 자체가 단위 오기(1,000배~1,000,000배)인 종목(032680·060310·065150·311390 등)을 재작성 우선으로 넣었고,
  1,000배 보류(_ofs.scale_jump)가 비율 1,000배 근처만 잡아 4분기 파생값(연간 − 세 분기)으로 새어 들어갔다.
판정: |새 값| > 10 × 그 회사 2020~2023 총자산 최대치(원 단위, 1조 원 이상 오기값 제외) 이고 |옛 값| ≤ 그 기준 → 옛 값으로. 정렬 스크립트에도 같은 검사(plausible) 추가.
"""
from __future__ import annotations
import argparse, collections, importlib.util, json, sys
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
_spec = importlib.util.spec_from_file_location("apply_ofs_extra", ROOT / "scripts" / "review" / "apply_ofs_extra_20261009.py")
_ofs = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_ofs)


def asset_scale(conn, code):
    """회사 규모 기준: 2020~2023 총자산 중앙값(오기값에 끌려가지 않게)."""
    v = sorted(abs(r[0]) for r in conn.execute("SELECT total_assets FROM financial_data WHERE stock_code=? AND year BETWEEN 2020 AND 2023 AND total_assets IS NOT NULL AND total_assets<>0", (code,)).fetchall())
    return v[len(v) // 2] if v else None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); a = ap.parse_args()
    conn = connect_primary_db(timeout=600, readonly=not a.apply)
    logs = conn.execute("""SELECT id, run_id, row_id, stock_code, year, quarter, report_type, field_name, old_value, new_value FROM financial_fix_log
        WHERE run_id LIKE 'align_raw_2021_2022_20261012_%' ORDER BY id""").fetchall()
    logs += [("cf",) + tuple(r[1:]) for r in conn.execute("""SELECT id, run_id, row_id, stock_code, year, quarter, report_type, field_name, old_value, new_value FROM cashflow_fix_log
        WHERE run_id LIKE 'align_raw_2021_2022_20261012_%' ORDER BY id""").fetchall()]
    first_old, last_new = {}, {}
    for lid, run, rid, code, y, q, fs, col, old, new in logs:
        tbl = "cash_flow_data" if lid == "cf" else "financial_data"
        k = (tbl, rid, col)
        first_old.setdefault(k, (old, code, y, q, fs))
        last_new[k] = new
    sc, plan = {}, []
    for k, (old, code, y, q, fs) in first_old.items():
        tbl, rid, col = k
        cur = conn.execute(f"SELECT {col} FROM {tbl} WHERE id=?", (rid,)).fetchone()
        if cur is None or cur[0] is None:
            continue
        if code not in sc:
            sc[code] = asset_scale(conn, code)
        s = sc[code]
        if not s:
            continue
        lim = 10 * s
        if abs(cur[0]) > lim and (old is None or abs(old) <= lim):
            plan.append((tbl, rid, code, y, q, fs, col, cur[0], old))
    res = {"at": datetime.now().isoformat(timespec="seconds"), "apply": a.apply, "cells_changed_today": len(first_old), "revert": len(plan),
           "by_code": dict(collections.Counter(p[2] for p in plan)), "sample": [p[2:] for p in plan[:15]]}
    if a.apply and plan:
        run_id = f"revert_implausible_{datetime.now():%Y%m%d_%H%M%S}"; now = datetime.now().isoformat(timespec="seconds")
        for tbl, log in (("financial_data", "financial_fix_log"), ("cash_flow_data", "cashflow_fix_log")):
            ps = [p for p in plan if p[0] == tbl]
            if not ps:
                continue
            bk = f"{tbl}_backup_revert_implausible_20261012"
            conn.execute(f"CREATE TABLE IF NOT EXISTS {bk} AS SELECT *, CAST(NULL AS TEXT) run_id FROM {tbl} WHERE false")
            ids = sorted({p[1] for p in ps})
            conn.execute(f"INSERT INTO {bk} SELECT *, ? FROM {tbl} WHERE id IN ({','.join('?' * len(ids))})", [run_id] + ids)
            for _, rid, code, y, q, fs, col, cur, old in ps:
                conn.execute(f"UPDATE {tbl} SET {col}=?, updated_at=? WHERE id=?", (old, now, rid))
            conn.execute(f"SELECT setval('{log}_id_seq', (SELECT COALESCE(MAX(id),1) FROM {log}))")
            conn.executemany(f"""INSERT INTO {log}(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             [(now, rid, code, y, q, 1 if q == 0 else 0, fs, col, cur, old, "규모상 불가능한 값(> 10 × 총자산 중앙값) — 10-12 정렬 전 값으로 되돌림(재작성 칸 단위 오기)",
                               "financial_fix_log 오늘 첫 old_value", run_id) for _, rid, code, y, q, fs, col, cur, old in ps])
        conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
        conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)""",
                     (now, "financial_data+cash_flow_data", "10-12 2021~22 정렬 중 규모상 불가능한 값 되돌림", len(plan), "> 10 × 총자산 중앙값",
                      json.dumps(res["by_code"], ensure_ascii=False), "", "scripts/review/revert_implausible_align_20261012.py", run_id))
        conn.commit()
        bad = [p for p in plan if (lambda v: (v is None) != (p[8] is None) or (v is not None and abs(v - p[8]) > 0.5))(conn.execute(f"SELECT {p[6]} FROM {p[0]} WHERE id=?", (p[1],)).fetchone()[0])]
        res.update({"run_id": run_id, "post_readback": {"planned": len(plan), "mismatch": len(bad), "pass": not bad},
                    "canonical_sync": {k: v for k, v in _ofs.canonical_sync(conn, run_id, now, {p[1] for p in plan if p[0] == "financial_data"},
                                                                            {p[1] for p in plan if p[0] == "cash_flow_data"}).items() if "sample" not in k}})
    print(json.dumps(res, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
