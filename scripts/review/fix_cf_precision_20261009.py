#!/usr/bin/env python3
"""현금흐름 정밀도 정정 — '확정'이지만 |DB − FnGuide| > 200만원인 영업·투자·재무 칸 (2026-10-09). 기본 dry-run.

배경: 검증 허용오차 max(200만원, 0.5%)가 억 단위 반올림값(예: 311390 2023 재무활동 −40억 vs 원문 −39.80억)을 '확정'으로 셌다.
후보(DART 원문 data_raw/dart_fnltt, 표준 ID 추출 financial_rereview_20261002.extract):
  연간(q0): 그 해 사업보고서 당기 / 다음 해 사업보고서 전기(재작성)
  1~3분기: 그 분기 보고서 누적(thstrm) − 직전 분기 누적 (1분기는 누적 = 3개월)  → 3개월 칸(*_q)과 누적 칸
  4분기  : 연간(당기 또는 재작성) − 3분기 보고서 누적 (FnGuide 방식, D-6)        → *_q, 누적 칸 = 연간
비교 대상: 검증표 값(연간 = 누적, 분기 = 3개월). 후보가 FnGuide와 200만원 이내일 때만 그 기간 원장 행 전부(연간 두 벌 포함)를 정정.
제외: 보고통화 외화·비12월 결산·단위 오류 종목, data_lock, 오태깅 종목(fs_quirk:cf_total_tag_mismatch — 별도 스크립트), 1,000배 급변.
적용: 백업 cash_flow_data_backup_cf_precision_20261009·cashflow_fix_log·data_fix_log, readback, 정본 동기화, 재측정.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import importlib.util
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "review"))
from db_compat import connect_primary_db  # noqa: E402
from financial_rereview_20261002 import extract  # noqa: E402

RAW = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/dart_fnltt")
K = {"operating_cf": "ocf", "investing_cf": "icf", "financing_cf": "fcf"}
TOL = 2e6
_spec = importlib.util.spec_from_file_location("apply_ofs_extra", ROOT / "scripts" / "review" / "apply_ofs_extra_20261009.py")
_ofs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ofs)
_cache = {}


def raw(code, y, q, fs, col="thstrm_amount"):
    key = (code, y, q, fs, col)
    if key not in _cache:
        p = RAW / code / f"{y}_{q}_{fs}.json.gz"
        _cache[key] = extract(json.loads(gzip.decompress(p.read_bytes())), col) if p.exists() else {}
    return _cache[key]


def annual_cands(code, y, fs, k):
    out = []
    v = raw(code, y, 0, fs).get(k)
    if v is not None:
        out.append(("연간 당기", v))
    v = raw(code, y + 1, 0, fs, "frmtrm_amount").get(k)
    if v is not None:
        out.append(("연간 재작성(다음 해 전기)", v))
    return out


def cands(code, y, q, fs, f):
    """[(라벨, 비교값, {칸: 새 값})]"""
    k = K[f]
    if q == 0:
        return [(lab, v, {f: v}) for lab, v in annual_cands(code, y, fs, k)]
    if q in (1, 2, 3):
        cum = raw(code, y, q, fs).get(k)
        prev = 0.0 if q == 1 else raw(code, y, q - 1, fs).get(k)
        if cum is None or prev is None:
            return []
        return [(f"{q}분기 누적 차분", cum - prev, {f: cum, f + "_q": cum - prev})]
    c3 = raw(code, y, 3, fs).get(k)
    if c3 is None:
        return []
    return [(f"Q4 = {lab} − 3분기 누적", av - c3, {f: av, f + "_q": av - c3}) for lab, av in annual_cands(code, y, fs, k)]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true"); a = ap.parse_args()
    conn = connect_primary_db(timeout=600, readonly=not a.apply)
    cells = [tuple(r) for r in conn.execute(f"""SELECT stock_code, year, quarter, report_type, field, db_value, fnguide_value FROM financial_field_verification
        WHERE status LIKE '확정%%' AND db_value IS NOT NULL AND fnguide_value IS NOT NULL AND ABS(db_value - fnguide_value) > {TOL}
        AND field IN ('operating_cf','investing_cf','financing_cf')""").fetchall()]
    excl = {r[0] for r in conn.execute("""SELECT stock_code FROM stock_collection_config WHERE config_key IN
        ('fs_quirk:reporting_currency','fs_quirk:unit_scale_fixed','fs_quirk:dart_unit_error','fs_quirk:cf_total_tag_mismatch')
        OR (config_key='fs_quirk:fiscal_month' AND config_value<>'12')""").fetchall()}
    locks = {(r[0], r[1]) for r in conn.execute("SELECT stock_code, year FROM data_lock WHERE is_locked=1 AND table_name='cash_flow_data'").fetchall()}
    plan, keep, st = [], [], collections.Counter()
    for code, y, q, fs, f, dv, fg in cells:
        if code in excl:
            st["제외 종목"] += 1; keep.append((code, y, q, fs, f, "제외 종목")); continue
        if (code, y) in locks:
            st["잠금"] += 1; keep.append((code, y, q, fs, f, "data_lock")); continue
        cs = cands(code, y, q, fs, f)
        if not cs:
            st["원문 없음"] += 1; keep.append((code, y, q, fs, f, "DART 원문 없음")); continue
        hit = [c for c in cs if abs(c[1] - fg) <= TOL]
        if not hit:
            st["원문 후보 ≠ FnGuide"] += 1
            keep.append((code, y, q, fs, f, "; ".join(f"{lab}={v:,.0f}" for lab, v, _ in cs) + f" ≠ FnGuide {fg:,.0f}")); continue
        lab, _, new = hit[0]
        cond = "is_annual" if q == 0 else "NOT is_annual AND quarter=?"
        rows = conn.execute(f"SELECT id, {f}, {f}_q FROM cash_flow_data WHERE stock_code=? AND year=? AND report_type=? AND {cond}",
                            (code, y, fs) if q == 0 else (code, y, fs, q)).fetchall()
        if not rows:
            st["원장 행 없음"] += 1; keep.append((code, y, q, fs, f, "원장 행 없음")); continue
        st[f"정정({lab.split(' ')[0]})"] += 1
        for rid, oc, oq in rows:
            for col, old in ((f, oc), (f + "_q", oq)):
                if col not in new or (old is not None and abs(old - new[col]) <= 0.5):
                    continue
                if old is not None and _ofs.scale_jump(old, new[col]):
                    st["1,000배 급변 — 보류"] += 1; continue
                plan.append((rid, code, y, q, fs, col, old, new[col], f"현금흐름 정밀도: {lab} = FnGuide(확정이었으나 200만원 초과 차이)"))
    res = {"at": datetime.now().isoformat(timespec="seconds"), "mode": "apply" if a.apply else "dry_run", "cells": len(cells),
           "stats": dict(st), "fix": len(plan), "keep": len(keep), "fix_sample": plan[:40], "keep_list": keep}
    if a.apply and plan:
        run_id = f"cf_precision_{datetime.now():%Y%m%d_%H%M%S}"
        now = datetime.now().isoformat(timespec="seconds")
        bk = "cash_flow_data_backup_cf_precision_20261009"
        conn.execute(f"CREATE TABLE IF NOT EXISTS {bk} AS SELECT *, CAST(NULL AS TEXT) run_id FROM cash_flow_data WHERE false")
        ids = sorted({p[0] for p in plan})
        conn.execute(f"INSERT INTO {bk} SELECT *, ? FROM cash_flow_data WHERE id IN ({','.join('?' * len(ids))})", [run_id] + ids)
        for rid, code, y, q, fs, col, old, new, rule in plan:
            conn.execute(f"UPDATE cash_flow_data SET {col}=?, updated_at=? WHERE id=?", (new, now, rid))
        conn.execute("SELECT setval('cashflow_fix_log_id_seq', (SELECT COALESCE(MAX(id),1) FROM cashflow_fix_log))")
        conn.executemany("""INSERT INTO cashflow_fix_log(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         [(now, rid, code, y, q, 1 if q == 0 else 0, fs, col, old, new, rule, "DART 원문(data_raw/dart_fnltt)", run_id)
                          for rid, code, y, q, fs, col, old, new, rule in plan])
        conn.execute("SELECT setval('data_fix_log_id_seq', (SELECT MAX(id) FROM data_fix_log))")
        conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                     (now, "cash_flow_data", "'확정'이나 DB−FnGuide 200만원 초과 현금흐름 칸(정밀도)", len(plan),
                      "원문 후보(연간 당기·재작성, 분기 누적 차분, Q4 = 연간 − 3분기 누적)가 FnGuide와 200만원 이내일 때만",
                      json.dumps(dict(st), ensure_ascii=False), "DART 원문값", "scripts/review/fix_cf_precision_20261009.py", run_id))
        conn.commit()
        bad = [p for p in plan if abs(float(conn.execute(f"SELECT {p[5]} FROM cash_flow_data WHERE id=?", (p[0],)).fetchone()[0]) - p[7]) > 0.5]
        res.update({"applied": True, "run_id": run_id, "post_readback": {"planned": len(plan), "mismatch": len(bad), "pass": not bad},
                    "canonical_sync": _ofs.canonical_sync(conn, run_id, now, set(), set(ids))})
        conn.close()
        pr = subprocess.run([sys.executable, str(ROOT / "scripts" / "review" / "build_field_verification_20261003.py")], capture_output=True, text=True, cwd=str(ROOT))
        res["field_verification_after"] = (pr.stdout or pr.stderr).strip().splitlines()[-1:]
    out = ROOT / "research_outputs" / "financial_rereview_20261002" / f"cf_precision_20261009_{'apply' if a.apply else 'dry'}.json"
    if out.exists() and a.apply:
        out = out.with_name(out.stem + f"_{datetime.now():%H%M%S}.json")
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print(json.dumps({k: res.get(k) for k in ("mode", "cells", "stats", "fix", "keep", "post_readback", "field_verification_after")}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
