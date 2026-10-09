#!/usr/bin/env python3
"""PLAN P9-a (D-4: 2021년부터 99.999% — 사용자 10-09) — 2021~2022 순이익·자본 '전체 → 지배주주' 기준 전환 (2026-10-09). 기본 dry-run.

이 기간은 FnGuide 표시 기간 밖이라 외부 확인이 불가 → 등급은 'DART 원문 + 내부 확인'(확정(외부)과 구분해 fix_rule에 표시).
대상(전부 만족): `apply_full_dart_cf_2016_2022_min2021_dry.json`(apply_dart_refetch dry-run 전체 목록)의 순이익·자본 변경 중
  ① 기존 DB 값 = 같은 보고서 DART 원문 '전체'(ni_total / equity_total) 값(max(100만원, 0.5%))
  ② 새 값 = 같은 보고서 DART 원문 '지배'(ni_parent / equity_parent — 표준 XBRL 계정 또는 '지배+비지배=전체' 검산 통과 값)
  → 정본 정의(FINANCIAL §2-1: CFS 순이익·자본 = 지배주주 기준)로 맞추는 정정. 그 외(Q4 파생·설명 안 되는 차이)는 보류.
제외: 외화·비12월·단위 오류 종목, data_lock, 재작성·환산 보호 칸, 1,000배 급변(apply_dart_refetch 단계에서 이미 적용).
적용: 백업 `financial_data_backup_p9a_20261009`, financial_fix_log, data_fix_log, readback, 정본 동기화(대표 행), 재측정, 결과 JSON.
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

D = ROOT / "research_outputs" / "financial_rereview_20261002"
_spec = importlib.util.spec_from_file_location("apply_ofs_extra", ROOT / "scripts" / "review" / "apply_ofs_extra_20261009.py")
_ofs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ofs)


def close(a, b):
    return a is not None and b is not None and abs(a - b) <= max(1e6, abs(b) * 0.005)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); a = ap.parse_args()
    full = json.load(open(D / "apply_full_dart_cf_2016_2022_min2021_dry.json"))
    raw = {}
    for line in open(D / "dart_cf_2016_2022.jsonl"):
        x = json.loads(line)
        if x.get("ok") and x.get("fs"):
            raw[(x["code"], x["year"], x["q"])] = x
    conn = connect_primary_db(timeout=900, readonly=not a.apply)
    excl = {r[0] for r in conn.execute("""SELECT stock_code FROM stock_collection_config WHERE config_key IN
        ('fs_quirk:reporting_currency','fs_quirk:unit_scale_fixed','fs_quirk:dart_unit_error')
        OR (config_key='fs_quirk:fiscal_month' AND config_value<>'12')""").fetchall()}
    locks = {(r[0], r[1]) for r in conn.execute("SELECT stock_code, year FROM data_lock WHERE is_locked=1 AND table_name='financial_data'").fetchall()}
    plan, st = [], collections.Counter()
    for rid, code, y, q, ia, fs, f, old, new, rule in full["fin"]:
        if f not in ("net_income", "total_equity") or y < 2021:
            continue
        qq = 0 if (ia or q == 0) else q
        v = (raw.get((code, y, qq)) or {}).get("vals") or {}
        tk, pk = ("ni_total", "ni_parent") if f == "net_income" else ("equity_total", "equity_parent")
        if code in excl or (code, y) in locks:
            st["제외(외화·비12월·단위·잠금)"] += 1; continue
        if not (close(old, v.get(tk)) and v.get(pk) is not None and abs(new - v[pk]) <= 0.5):
            st["보류: 기존 ≠ 원문 전체 또는 새 값 ≠ 원문 지배"] += 1; continue
        cur = conn.execute(f"SELECT {f} FROM financial_data WHERE id=?", (rid,)).fetchone()
        if not cur or cur[0] is None or abs(float(cur[0]) - float(old)) > 0.5:
            st["보류: 원장 값이 dry-run 이후 바뀜"] += 1; continue
        st["적용: 전체 → 지배(원문 내부 확인)"] += 1
        plan.append((rid, code, y, q, ia, fs, f, old, new))
    res = {"at": datetime.now().isoformat(timespec="seconds"), "mode": "apply" if a.apply else "dry_run", "stats": dict(st),
           "planned": len(plan), "by_field": dict(collections.Counter(p[6] for p in plan)), "sample": plan[:30]}
    if a.apply and plan:
        run_id = f"p9a_parent_basis_{datetime.now():%Y%m%d_%H%M%S}"
        now = datetime.now().isoformat(timespec="seconds")
        bk = "financial_data_backup_p9a_20261009"
        conn.execute(f"CREATE TABLE IF NOT EXISTS {bk} AS SELECT *, CAST(NULL AS TEXT) run_id FROM financial_data WHERE false")
        ids = sorted({p[0] for p in plan})
        for i in range(0, len(ids), 1000):
            part = ids[i:i + 1000]
            conn.execute(f"INSERT INTO {bk} SELECT *, ? FROM financial_data WHERE id IN ({','.join('?' * len(part))})", [run_id] + part)
        for rid, code, y, q, ia, fs, f, old, new in plan:
            conn.execute(f"UPDATE financial_data SET {f}=?, updated_at=? WHERE id=?", (new, now, rid))
        conn.execute("SELECT setval('financial_fix_log_id_seq', (SELECT COALESCE(MAX(id),1) FROM financial_fix_log))")
        conn.executemany("""INSERT INTO financial_fix_log(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         [(now, rid, code, y, q, 1 if ia else 0, fs, f, old, new,
                           "P9-a 등급 'DART 원문 + 내부 확인'(외부 확인 기간 밖): 기존 = 원문 전체 → 원문 지배(§2-1)",
                           "DART fnlttSinglAcntAll(dart_cf_2016_2022.jsonl)", run_id) for rid, code, y, q, ia, fs, f, old, new in plan])
        conn.execute("SELECT setval('data_fix_log_id_seq', (SELECT MAX(id) FROM data_fix_log))")
        conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                     (now, "financial_data", "2021~2022 순이익·자본 지배주주 기준 전환", len(plan),
                      "기존 = DART 원문 전체, 새 값 = 같은 원문 지배(외부 확인 기간 밖 — 'DART 원문 + 내부 확인' 등급)",
                      json.dumps(dict(st), ensure_ascii=False)[:900], json.dumps(res["by_field"]), "scripts/review/apply_2021_parent_basis_20261009.py", run_id))
        conn.commit()
        bad = [p[0] for p in plan if abs(float(conn.execute(f"SELECT {p[6]} FROM financial_data WHERE id=?", (p[0],)).fetchone()[0]) - p[8]) > 0.5]
        res["post_readback"] = {"planned": len(plan), "mismatch": len(bad), "pass": not bad}
        res["canonical_sync"] = _ofs.canonical_sync(conn, run_id, now, set(ids), set())
        res["applied"], res["run_id"] = True, run_id
        conn.close()
        pr = subprocess.run([sys.executable, str(ROOT / "scripts" / "review" / "build_field_verification_20261003.py")],
                            capture_output=True, text=True, cwd=str(ROOT))
        res["field_verification_after"] = (pr.stdout or pr.stderr).strip().splitlines()[-1:]
        print("readback", res["post_readback"], "정본", {k: v for k, v in res["canonical_sync"].items() if not k.endswith("sample")},
              "재측정", res["field_verification_after"], flush=True)
    (D / f"p9a_parent_basis_20261009_{'apply' if a.apply else 'dry'}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print(json.dumps({k: res[k] for k in ("mode", "stats", "planned", "by_field")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
