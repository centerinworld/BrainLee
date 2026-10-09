#!/usr/bin/env python3
"""PLAN_99_99_NUMERIC P4(1순위: 손익·재무상태) — 현행 검증 '원인 조사' 칸을 DART 원문 후보로 칸마다 원인 판정 (2026-10-09). 기본 dry-run.

칸마다 원문 후보(같은 구분·같은 기간):
  연간: 당기(그 해 사업보고서) / 재작성(다음 해 사업보고서 전기 칸, §2-5 표시값 = 재작성값)
  Q1~Q3: 그 분기 보고서 값 / Q2·Q3는 누적 차분(이번 누적 − 직전 누적)
  Q4: 손익 = 연간 − 3분기 누적(FnGuide 방식, D-6) 및 연간 − Q1~Q3 합 / 재무상태 = 연간
  순이익·자본(CFS): 지배(정본 정의) / 전체(FnGuide가 전체로 표시하는 경우 = 정의 차이)
판정(우선순위):
  A 'DB 오류 — 원문(정본 정의) = FnGuide' → 정정 대상(--apply). 근거: DART 원문 + FnGuide 2소스(2-0 구조)
  B '정의 차이 — FnGuide = 전체(지배 아님)' → 값 유지, 설명된 예외 후보(D-1)
  C '원문 = DB, FnGuide 단독 다름' → 값 유지, FnGuide 쪽 정의·오류 후보
  D '원문 후보 전부 FnGuide·DB와 다름' → 사람 판정 표
  E '원문 없음' → 재수집 대상
  제외: 외화·비12월·단위 오류 종목, 보호 칸, 잠금, 1,000배 급변(정정만 제외, 판정은 기록)
결과는 칸별로 `financial_field_cause_review`(종목·연도·분기·구분·필드·판정·근거·run_id)에 기록 — 매일 재구축되는 검증표의 cause를 덮지 않고 따로 남긴다.
일치 기준: |후보 − FnGuide| ≤ 200만원(FnGuide 백만원 반올림 + 파생 누적). 차이 크기로 닫지 않는다.
사용: classify_fix_unclassified_fin_20261009.py [--apply]  (--apply = 판정 기록 + A 정정)
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

SRCS = [ROOT / "research_outputs" / "financial_rereview_20261002" / n for n in ("dart_cf_full.jsonl", "dart_cf_ofs_extra.jsonl")]
RAW_DIR = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/dart_fnltt")
FIELDS = ("revenue", "operating_profit", "net_income", "total_assets", "total_liabilities", "total_equity")
FLOW = {"revenue", "operating_profit", "net_income"}
TOL = 2e6
_spec = importlib.util.spec_from_file_location("apply_ofs_extra", ROOT / "scripts" / "review" / "apply_ofs_extra_20261009.py")
_ofs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ofs)


def keys_for(f, fs):
    """(정본 정의 키, 다른 정의 키) — CFS 순이익·자본은 지배가 정본(§2-1)."""
    if f == "net_income":
        return ("ni_parent", "ni_total") if fs == "CFS" else ("ni_total", None)
    if f == "total_equity":
        return ("equity_parent", "equity_total") if fs == "CFS" else ("equity_total", None)
    return (f, None)


def load_raw(codes):
    t = {}
    for p in SRCS:
        if not p.exists():
            continue
        for line in open(p):
            d = json.loads(line)
            if d.get("ok") and d.get("fs") and d["code"] in codes:
                t[(d["code"], d["year"], d["q"], d["fs"])] = d
    return t


_ytd = {}


def ytd(code, y, q, fs, k):
    key = (code, y, q, fs)
    if key not in _ytd:
        p = RAW_DIR / code / f"{y}_{q}_{fs}.json.gz"
        _ytd[key] = extract(json.loads(gzip.decompress(p.read_bytes())), "thstrm_add_amount") if p.exists() else {}
    return _ytd[key].get(k)


def cands(raw, code, y, q, fs, k):
    g = lambda yy, qq: raw.get((code, yy, qq, fs))  # noqa: E731
    v = lambda d, col="vals": ((d or {}).get(col) or {}).get(k)  # noqa: E731
    out = []
    if q == 0:
        if v(g(y, 0)) is not None:
            out.append(("연간 당기", v(g(y, 0))))
        if v(g(y + 1, 0), "vals_prev") is not None:
            out.append(("연간 재작성(다음 해 전기 칸)", v(g(y + 1, 0), "vals_prev")))
        return out
    flow = k in ("revenue", "operating_profit", "ni_parent", "ni_total")
    if q in (1, 2, 3):
        if v(g(y, q)) is not None:
            out.append((f"{q}분기 보고서", v(g(y, q))))
        if flow and q in (2, 3):
            cur, prev = ytd(code, y, q, fs, k), (ytd(code, y, q - 1, fs, k) if q == 3 else v(g(y, 1)))
            if cur is not None and prev is not None:
                out.append((f"{q}분기 누적 차분", cur - prev))
        return out
    # Q4
    anns = cands(raw, code, y, 0, fs, k)
    if not flow:
        return [(f"Q4 재무상태 = {lab}", x) for lab, x in anns]
    y3 = ytd(code, y, 3, fs, k)
    for lab, a in anns:
        if y3 is not None:
            out.append((f"Q4 = {lab} − 3분기 누적(FnGuide 방식)", a - y3))
        qs = [v(g(y, i)) for i in (1, 2, 3)]
        if all(x is not None for x in qs):
            out.append((f"Q4 = {lab} − Q1~Q3 합", a - sum(qs)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    conn = connect_primary_db(timeout=900, readonly=not a.apply)
    cells = [tuple(r) for r in conn.execute(f"""SELECT stock_code, year, quarter, report_type, field, db_value, fnguide_value, naver_value, cause
        FROM financial_field_verification WHERE status LIKE '원인%%' AND field IN ({','.join('?' * len(FIELDS))})""", FIELDS).fetchall()]
    excl = {r[0] for r in conn.execute("""SELECT stock_code FROM stock_collection_config WHERE config_key IN
        ('fs_quirk:reporting_currency','fs_quirk:unit_scale_fixed','fs_quirk:dart_unit_error')
        OR (config_key='fs_quirk:fiscal_month' AND config_value<>'12')""").fetchall()}
    locks = {(r[0], r[1]) for r in conn.execute("SELECT stock_code, year FROM data_lock WHERE is_locked=1 AND table_name='financial_data'").fetchall()}
    protected = {(r[0], r[1]) for r in conn.execute(
        "SELECT row_id, field_name FROM financial_fix_log WHERE run_id LIKE 'restated_annual_%%' OR run_id LIKE 'fx_krw_%%'").fetchall()}
    raw = load_raw({c[0] for c in cells})
    verdicts, plan, st = [], [], collections.Counter()
    for code, y, q, fs, f, dv, fg, nv, cause0 in cells:
        k_std, k_alt = keys_for(f, fs)
        cs = cands(raw, code, y, q, fs, k_std)
        ca = cands(raw, code, y, q, fs, k_alt) if k_alt else []
        hit = [(lab, x) for lab, x in cs if abs(x - fg) <= TOL]
        if code in excl:
            verdict, ev = "제외 종목(외화·비12월·단위)", ""
        elif not cs and not ca:
            verdict, ev = "E 원문 없음", "재수집 대상"
        elif hit:
            verdict, ev = "A DB 오류 — 원문(정본 정의) = FnGuide", f"{hit[0][0]}={hit[0][1]:,.0f}"
        elif any(abs(x - fg) <= TOL for _, x in ca):
            lab, x = [(l_, x_) for l_, x_ in ca if abs(x_ - fg) <= TOL][0]
            verdict, ev = "B 정의 차이 — FnGuide = 전체(지배 아님)", f"{lab}(전체)={x:,.0f}"
        elif dv is not None and any(abs(x - dv) <= TOL for _, x in cs):
            verdict, ev = "C 원문 = DB, FnGuide 단독 다름", "; ".join(f"{l_}={x:,.0f}" for l_, x in cs[:3])
        else:
            verdict, ev = "D 원문 후보 전부 다름 — 사람 판정", "; ".join(f"{l_}={x:,.0f}" for l_, x in (cs + ca)[:4])
        st[verdict] += 1
        verdicts.append((code, y, q, fs, f, dv, fg, nv, verdict, ev, cause0))
        if verdict.startswith("A") and (code, y) in locks:
            st["A 중 잠금(data_lock) 보류 — 정정 안 함"] += 1
        if verdict.startswith("A") and (code, y) not in locks:
            # 2026-10-09 검토 반영: 연간은 원장 행이 quarter 0·4 두 벌일 수 있다 → 같은 기간 행 전부를 같은 확인값으로, 값이 이미 같으면 제외(no-op)
            rows = conn.execute(f"""SELECT id, {f} FROM financial_data WHERE stock_code=? AND year=? AND report_type=?
                                   AND {'is_annual' if q == 0 else 'NOT is_annual AND quarter=?'} ORDER BY id""",
                                (code, y, fs) if q == 0 else (code, y, fs, q)).fetchall()
            if not rows:
                st["A 중 정정 제외(원장 없음)"] += 1
            for row in rows:
                if row[1] is not None and abs(float(row[1]) - hit[0][1]) <= 0.5:
                    st["A 중 이미 같은 행(no-op 제외)"] += 1
                elif (row[0], f) in protected or _ofs.scale_jump(row[1], hit[0][1]):
                    st["A 중 정정 제외(보호·1,000배)"] += 1
                else:
                    plan.append((row[0], code, y, q, fs, f, row[1], hit[0][1], f"P4: {hit[0][0]} = FnGuide(DART 원문 + FnGuide 2소스)"))
    by_field = collections.Counter((v[4], v[8][:1]) for v in verdicts)
    res = {"at": datetime.now().isoformat(timespec="seconds"), "mode": "apply" if a.apply else "dry_run", "cells": len(cells),
           "verdicts": dict(st), "by_field_verdict": {f"{k[0]}|{k[1]}": n for k, n in by_field.items()},
           "fix": len(plan), "fix_sample": plan[:30], "sample_by_verdict": {}}
    for v in verdicts:
        res["sample_by_verdict"].setdefault(v[8], [])
        if len(res["sample_by_verdict"][v[8]]) < 8:
            res["sample_by_verdict"][v[8]].append(v)
    if a.apply:
        run_id = f"p4_fin_cause_{datetime.now():%Y%m%d_%H%M%S}"
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute("""CREATE TABLE IF NOT EXISTS financial_field_cause_review (stock_code TEXT, year INT, quarter INT, report_type TEXT, field TEXT,
                        db_value DOUBLE PRECISION, fnguide_value DOUBLE PRECISION, naver_value DOUBLE PRECISION, verdict TEXT, evidence TEXT,
                        prior_cause TEXT, run_id TEXT, created_at TEXT)""")
        conn.executemany("INSERT INTO financial_field_cause_review VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         [v + (run_id, now) for v in verdicts])
        if plan:
            bk = "financial_data_backup_p4_20261009"
            conn.execute(f"CREATE TABLE IF NOT EXISTS {bk} AS SELECT *, CAST(NULL AS TEXT) run_id FROM financial_data WHERE false")
            ids = sorted({p[0] for p in plan})
            for i in range(0, len(ids), 1000):
                part = ids[i:i + 1000]
                conn.execute(f"INSERT INTO {bk} SELECT *, ? FROM financial_data WHERE id IN ({','.join('?' * len(part))})", [run_id] + part)
            for rid, code, y, q, fs, f, old, new, rule in plan:
                conn.execute(f"UPDATE financial_data SET {f}=?, updated_at=? WHERE id=?", (new, now, rid))
            conn.execute("SELECT setval('financial_fix_log_id_seq', (SELECT COALESCE(MAX(id),1) FROM financial_fix_log))")
            conn.executemany("""INSERT INTO financial_fix_log(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             [(now, rid, code, y, q, 1 if q == 0 else 0, fs, f, old, new, rule, "DART 원문(dart_cf_full·ofs_extra·data_raw/dart_fnltt 누적 칸)", run_id)
                              for rid, code, y, q, fs, f, old, new, rule in plan])
        conn.execute("SELECT setval('data_fix_log_id_seq', (SELECT MAX(id) FROM data_fix_log))")
        conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                     (now, "financial_data+financial_field_cause_review", f"현행 검증 원인 조사 손익·재무상태 {len(cells)}칸", len(plan),
                      "칸별 원인 판정 기록 + 'DART 원문(정본 정의) = FnGuide'인 칸만 원문값으로 정정",
                      json.dumps(dict(st), ensure_ascii=False)[:900], f"정정 {len(plan)}", "scripts/review/classify_fix_unclassified_fin_20261009.py", run_id))
        conn.commit()
        bad = [(p[0], p[5]) for p in plan
               if abs(float(conn.execute(f"SELECT {p[5]} FROM financial_data WHERE id=?", (p[0],)).fetchone()[0]) - p[7]) > 0.5]
        res["post_readback"] = {"planned": len(plan), "mismatch": len(bad), "verdict_rows": conn.execute(
            "SELECT COUNT(*) FROM financial_field_cause_review WHERE run_id=?", (run_id,)).fetchone()[0], "pass": not bad}
        res["canonical_sync"] = _ofs.canonical_sync(conn, run_id, now, {p[0] for p in plan}, set()) if plan else {"pass": True}
        res["applied"], res["run_id"] = True, run_id
        conn.close()
        pr = subprocess.run([sys.executable, str(ROOT / "scripts" / "review" / "build_field_verification_20261003.py")],
                            capture_output=True, text=True, cwd=str(ROOT))
        res["field_verification_after"] = (pr.stdout or pr.stderr).strip().splitlines()[-1:]
        print("readback", res["post_readback"], "정본", {k: v for k, v in res["canonical_sync"].items() if not k.endswith("sample")},
              "재측정", res["field_verification_after"], flush=True)
    out = ROOT / "research_outputs" / "financial_rereview_20261002" / f"p4_fin_cause_20261009_{'apply' if a.apply else 'dry'}.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print(json.dumps({k: res[k] for k in ("mode", "cells", "verdicts", "fix")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
