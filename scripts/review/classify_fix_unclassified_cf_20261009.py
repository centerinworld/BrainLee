#!/usr/bin/env python3
"""PLAN_99_99_NUMERIC P4-2(1순위: 현금흐름 영업·투자·재무) — 현행 검증 '원인 조사' 칸을 DART 원문 후보로 판정 (2026-10-09). 기본 dry-run.

현금흐름 원문(재수집 `vals`)은 누적(YTD). 검증표는 연간 = 누적 칸, 분기 = 3개월 칸(`*_q`)을 FnGuide와 비교한다.
후보: 연간 = 사업보고서 / 다음 해 전기 칸(재작성) · Q1 = 누적 · Q2·Q3 = 누적 차분 · Q4 = 연간 − 3분기 누적.
판정: A 원문 = FnGuide → 정정(분기는 3개월 칸 + 같은 행 누적 칸을 원문 누적으로, Q4는 누적 칸 = 연간) / C 원문 = DB, FnGuide 단독 다름 /
      D 원문 후보 전부 다름(사람 판정) / E 원문 없음 / 제외 종목. 일치 기준 200만원. 판정은 `financial_field_cause_review`에 기록.
적용: cash_flow_data 백업(`cash_flow_data_backup_p4cf_20261009`)·cashflow_fix_log·data_fix_log·readback·정본(현금흐름) 동기화·재측정.
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

SRCS = [ROOT / "research_outputs" / "financial_rereview_20261002" / n for n in ("dart_cf_full.jsonl", "dart_cf_ofs_extra.jsonl", "dart_cf_ofs_extra_annual_2023_2024.jsonl")]
FMAP = {"operating_cf": "ocf", "investing_cf": "icf", "financing_cf": "fcf"}
TOL = 2e6
_spec = importlib.util.spec_from_file_location("apply_ofs_extra", ROOT / "scripts" / "review" / "apply_ofs_extra_20261009.py")
_ofs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ofs)


def load_raw(codes):
    t = {}
    for p in SRCS:
        if p.exists():
            for line in open(p):
                d = json.loads(line)
                if d.get("ok") and d.get("fs") and d["code"] in codes:
                    t[(d["code"], d["year"], d["q"], d["fs"])] = d
    return t


def cands(raw, code, y, q, fs, k):
    """[(라벨, 비교값(연간=누적·분기=3개월), 그 행의 누적 칸 값)]"""
    v = lambda yy, qq, col="vals": ((raw.get((code, yy, qq, fs)) or {}).get(col) or {}).get(k)  # noqa: E731
    out = []
    if q == 0:
        if v(y, 0) is not None:
            out.append(("연간 당기", v(y, 0), v(y, 0)))
        if v(y + 1, 0, "vals_prev") is not None:
            out.append(("연간 재작성(다음 해 전기 칸)", v(y + 1, 0, "vals_prev"), v(y + 1, 0, "vals_prev")))
    elif q == 1:
        if v(y, 1) is not None:
            out.append(("1분기 누적", v(y, 1), v(y, 1)))
    elif q in (2, 3):
        if v(y, q) is not None and v(y, q - 1) is not None:
            out.append((f"{q}분기 = 누적 차분", v(y, q) - v(y, q - 1), v(y, q)))
    elif q == 4:
        for lab, a, _ in cands(raw, code, y, 0, fs, k):
            if v(y, 3) is not None:
                out.append((f"Q4 = {lab} − 3분기 누적", a - v(y, 3), a))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    conn = connect_primary_db(timeout=900, readonly=not a.apply)
    cells = [tuple(r) for r in conn.execute("""SELECT stock_code, year, quarter, report_type, field, db_value, fnguide_value, naver_value, cause
        FROM financial_field_verification WHERE status LIKE '원인%%' AND field IN ('operating_cf','investing_cf','financing_cf')""").fetchall()]
    excl = {r[0] for r in conn.execute("""SELECT stock_code FROM stock_collection_config WHERE config_key IN
        ('fs_quirk:reporting_currency','fs_quirk:unit_scale_fixed','fs_quirk:dart_unit_error')
        OR (config_key='fs_quirk:fiscal_month' AND config_value<>'12')""").fetchall()}
    locks = {(r[0], r[1]) for r in conn.execute("SELECT stock_code, year FROM data_lock WHERE is_locked=1 AND table_name='cash_flow_data'").fetchall()}
    protected = {(r[0], r[1]) for r in conn.execute(
        "SELECT row_id, field_name FROM cashflow_fix_log WHERE run_id LIKE 'restated_annual_%%' OR run_id LIKE 'fx_krw_%%'").fetchall()}
    raw = load_raw({c[0] for c in cells})
    verdicts, plan, st = [], [], collections.Counter()
    act = collections.Counter()  # 2026-10-09 검토 반영: 판정(st)은 칸마다 하나, 정정·보류 사유는 act로 따로(이중 집계 금지)
    for code, y, q, fs, f, dv, fg, nv, cause0 in cells:
        cs = cands(raw, code, y, q, fs, FMAP[f])
        hit = [c for c in cs if abs(c[1] - fg) <= TOL]
        if code in excl:
            verdict, ev = "제외 종목(외화·비12월·단위)", ""
        elif not cs:
            verdict, ev = "E 원문 없음", "재수집 대상"
        elif hit:
            verdict, ev = "A DB 오류 — 원문 = FnGuide", f"{hit[0][0]}={hit[0][1]:,.0f}"
        elif dv is not None and any(abs(c[1] - dv) <= TOL for c in cs):
            verdict, ev = "C 원문 = DB, FnGuide 단독 다름", "; ".join(f"{c[0]}={c[1]:,.0f}" for c in cs[:3])
        else:
            verdict, ev = "D 원문 후보 전부 다름 — 사람 판정", "; ".join(f"{c[0]}={c[1]:,.0f}" for c in cs[:3])
        st[verdict] += 1
        verdicts.append((code, y, q, fs, f, dv, fg, nv, verdict, ev, cause0))
        if not verdict.startswith("A"):
            continue
        if (code, y) in locks:
            act["A 중 잠금(data_lock) 보류 — 정정 안 함"] += 1  # 2026-10-09 검토 반영: A 잔여를 사유별로 분리
            continue
        n_before = len(plan)
        lab, cmpv, cumv = hit[0]
        cond = "is_annual" if q == 0 else "NOT is_annual AND quarter=?"
        rows = conn.execute(f"SELECT id, {f}, {f}_q FROM cash_flow_data WHERE stock_code=? AND year=? AND report_type=? AND {cond}",
                            (code, y, fs) if q == 0 else (code, y, fs, q)).fetchall()
        if not rows:
            act["A 중 원장 행 없음"] += 1
            continue
        for r in rows:
            changes = []
            if q == 0:
                changes.append((f, r[1], cumv))
            else:
                changes.append((f + "_q", r[2], cmpv))
                changes.append((f, r[1], cumv))
            for col, old, new in changes:
                if old is not None and abs(old - new) <= 0.5:
                    continue
                if ("cash_flow_data", r[0], col) in {("cash_flow_data",) + p for p in protected} or _ofs.scale_jump(old, new):
                    act["A 중 보호·1,000배 제외"] += 1
                    continue
                plan.append((r[0], code, y, q, fs, col, old, new, f"P4-2: {lab} = FnGuide(DART 원문 + FnGuide 2소스)"))
        if len(plan) == n_before:
            act["A 중 이미 원문값(no-op) — 정정 안 함"] += 1
    res = {"at": datetime.now().isoformat(timespec="seconds"), "mode": "apply" if a.apply else "dry_run", "cells": len(cells),
           "verdicts": dict(st), "actions": dict(act), "verdict_total": sum(st.values()), "fix_cells": len(plan), "fix_sample": plan[:30], "sample_by_verdict": {}}
    for v in verdicts:
        res["sample_by_verdict"].setdefault(v[8], [])
        if len(res["sample_by_verdict"][v[8]]) < 8:
            res["sample_by_verdict"][v[8]].append(v)
    if a.apply:
        run_id = f"p4cf_cause_{datetime.now():%Y%m%d_%H%M%S}"
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute("""CREATE TABLE IF NOT EXISTS financial_field_cause_review (stock_code TEXT, year INT, quarter INT, report_type TEXT, field TEXT,
                        db_value DOUBLE PRECISION, fnguide_value DOUBLE PRECISION, naver_value DOUBLE PRECISION, verdict TEXT, evidence TEXT,
                        prior_cause TEXT, run_id TEXT, created_at TEXT)""")
        conn.executemany("INSERT INTO financial_field_cause_review VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [v + (run_id, now) for v in verdicts])
        ids = sorted({p[0] for p in plan})
        if plan:
            bk = "cash_flow_data_backup_p4cf_20261009"
            conn.execute(f"CREATE TABLE IF NOT EXISTS {bk} AS SELECT *, CAST(NULL AS TEXT) run_id FROM cash_flow_data WHERE false")
            for i in range(0, len(ids), 1000):
                part = ids[i:i + 1000]
                conn.execute(f"INSERT INTO {bk} SELECT *, ? FROM cash_flow_data WHERE id IN ({','.join('?' * len(part))})", [run_id] + part)
            for rid, code, y, q, fs, col, old, new, rule in plan:
                conn.execute(f"UPDATE cash_flow_data SET {col}=?, updated_at=? WHERE id=?", (new, now, rid))
            conn.execute("SELECT setval('cashflow_fix_log_id_seq', (SELECT COALESCE(MAX(id),1) FROM cashflow_fix_log))")
            conn.executemany("""INSERT INTO cashflow_fix_log(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             [(now, rid, code, y, q, 1 if q == 0 else 0, fs, col, old, new, rule, "DART 원문(dart_cf_full·ofs_extra)", run_id)
                              for rid, code, y, q, fs, col, old, new, rule in plan])
        conn.execute("SELECT setval('data_fix_log_id_seq', (SELECT MAX(id) FROM data_fix_log))")
        conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                     (now, "cash_flow_data+financial_field_cause_review", f"현행 검증 원인 조사 현금흐름 {len(cells)}칸", len(plan),
                      "칸별 원인 판정 기록 + 'DART 원문 = FnGuide'인 칸만 원문값(분기 3개월·누적)으로",
                      json.dumps({"verdicts": dict(st), "actions": dict(act)}, ensure_ascii=False)[:900], f"정정 {len(plan)}칸", "scripts/review/classify_fix_unclassified_cf_20261009.py", run_id))
        conn.commit()
        bad = [(p[0], p[5]) for p in plan
               if abs(float(conn.execute(f"SELECT {p[5]} FROM cash_flow_data WHERE id=?", (p[0],)).fetchone()[0]) - p[7]) > 0.5]
        res["post_readback"] = {"planned": len(plan), "mismatch": len(bad), "pass": not bad}
        res["canonical_sync"] = _ofs.canonical_sync(conn, run_id, now, set(), set(ids)) if plan else {"pass": True}
        res["applied"], res["run_id"] = True, run_id
        conn.close()
        pr = subprocess.run([sys.executable, str(ROOT / "scripts" / "review" / "build_field_verification_20261003.py")],
                            capture_output=True, text=True, cwd=str(ROOT))
        res["field_verification_after"] = (pr.stdout or pr.stderr).strip().splitlines()[-1:]
        print("readback", res["post_readback"], "정본", {k: v for k, v in res["canonical_sync"].items() if not k.endswith("sample")},
              "재측정", res["field_verification_after"], flush=True)
    out = ROOT / "research_outputs" / "financial_rereview_20261002" / f"p4cf_cause_20261009_{'apply' if a.apply else 'dry'}.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print(json.dumps({k: res[k] for k in ("mode", "cells", "verdict_total", "verdicts", "actions", "fix_cells")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
