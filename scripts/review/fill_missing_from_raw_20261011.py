#!/usr/bin/env python3
"""원인군 DB_MISSING_CFS / DB_MISSING_OFS — 원문은 있는데 원장 칸이 빈 경우 채움 (2026-10-11). 기본 dry-run.

대상: 고정 분모(financial_field_baseline 최신) ∩ P1 필드 ∩ '미확인(DB 없음)' ∩ 리츠·외화 제외. 비12월 결산은 10-11부터 포함(fin_norm.raw가 회계 키 → DART 키 변환).
후보(모두 fin_norm 공통 함수 — 정의 한 곳):
  연간: latest_annual(재작성 우선) 과 원본 당기 둘 다 후보
  1~3분기 손익: latest_quarter_3m / 재무상태: 그 분기 보고서 당기 / 현금흐름: latest_cf_cum 누적 → cf_three_month
  4분기 손익: q4_flow(연간, ytd3) / 재무상태: 연간 / 현금흐름: 누적 = 연간, 3개월 = 연간 − 3분기 누적
반영: 후보가 그 칸 FnGuide 값과 200만원 이내일 때만(원칙 0). 원장 행이 있으면 빈 칸 UPDATE(연간 두 벌 포함), 행이 없으면 '행 없음' 목록(삽입은 별도 단계).
적용: 백업·fix_log·data_fix_log·readback·정본 동기화·재측정. 출처 표시 '+fill_raw_20261011'.
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

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "review"))
from db_compat import connect_primary_db  # noqa: E402
import fin_norm as fn  # noqa: E402

OUT = ROOT / "research_outputs" / "financial_rereview_20261002"
TOL = 2e6
FIN = ("revenue", "operating_profit", "net_income", "total_assets", "total_liabilities", "total_equity")
CF = fn.FLOW_CF
TAG = "+fill_raw_20261011"
_spec = importlib.util.spec_from_file_location("apply_ofs_extra", ROOT / "scripts" / "review" / "apply_ofs_extra_20261009.py")
_ofs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ofs)


def candidates(code, y, q, fs, f):
    """[(라벨, {원장 칸: 값}, 비교값)]"""
    out = []
    if q == 0:
        seen = set()
        for lab, (v, src) in (("연간 재작성 우선", fn.latest_annual(code, y, fs, f)), ("연간 원본", (fn.raw(code, y, 0, fs, f), "original"))):
            if v is not None and v not in seen:
                seen.add(v); out.append((lab, {f: v}, v))
        return out
    if f in CF:
        A = fn.raw(code, y, 0, fs, f) if q == 4 else None
        if q == 4:
            y3 = fn.ytd3(code, y, fs, f)
            if A is not None and y3 is not None:
                out.append(("4분기 = 연간 − 3분기 누적", {f: A, f + "_q": fn.q4_flow(A, y3)}, fn.q4_flow(A, y3)))
            return out
        cum, _ = fn.latest_cf_cum(code, y, q, fs, f)
        prev = fn.latest_cf_cum(code, y, q - 1, fs, f)[0] if q > 1 else None
        three = fn.cf_three_month(cum, prev, q)
        if cum is not None and three is not None:
            out.append((f"{q}분기 누적 최신판 차분", {f: cum, f + "_q": three}, three))
        return out
    if f in fn.BS:
        v = fn.raw(code, y, 0 if q == 4 else q, fs, f)
        if v is not None:
            out.append(("재무상태 = 그 시점 보고서" if q != 4 else "4분기 재무상태 = 연간", {f: v}, v))
        return out
    if q == 4:
        A = fn.raw(code, y, 0, fs, f); y3 = fn.ytd3(code, y, fs, f)
        v = fn.q4_flow(A, y3)
        if v is not None:
            out.append(("4분기 = 연간 − 3분기 누적", {f: v}, v))
        return out
    v, src = fn.latest_quarter_3m(code, y, q, fs, f)
    if v is not None:
        out.append((f"{q}분기 3개월({src})", {f: v}, v))
    v0 = fn.raw(code, y, q, fs, f)
    if v0 is not None and v0 != v:
        out.append((f"{q}분기 3개월(원본)", {f: v0}, v0))
    return out


def doc_cands(code, y, q, fs, f, docs):
    """본문 후보(연결·별도 모두). 연간은 API 원문 → 없으면 본문. 4분기 = 연간 − 3분기 누적(fin_norm)."""
    d = lambda qq: docs.get((code, y, qq, fs, f))  # noqa: E731 — (3개월, 누적)
    ann = fn.raw(code, y, 0, fs, f)
    if ann is None and d(0):
        ann = d(0)[1]
    if q == 0:
        return [("사업보고서 본문 재무제표", {f: ann}, ann)] if ann is not None else []
    if f in fn.BS:
        v = ann if q == 4 else (d(q) or (None, None))[0]
        return [("본문 재무상태" if q != 4 else "4분기 재무상태 = 연간", {f: v}, v)] if v is not None else []
    if f in CF:
        def cum(qq):
            v = fn.raw(code, y, qq, fs, f)
            return v if v is not None else (d(qq) or (None, None))[1]
        if q == 4:
            v = fn.q4_flow(ann, cum(3))
            return [("4분기 = 연간 − 3분기 누적(본문)", {f: ann, f + "_q": v}, v)] if v is not None else []
        c, p_ = cum(q), (cum(q - 1) if q > 1 else None)
        three = fn.cf_three_month(c, p_, q)
        return [(f"{q}분기 누적 차분(본문)", {f: c, f + "_q": three}, three)] if c is not None and three is not None else []
    if q == 4:
        c3 = fn.ytd3(code, y, fs, f)
        if c3 is None and d(3):
            c3 = d(3)[1]
        v = fn.q4_flow(ann, c3)
        return [("4분기 = 연간 − 3분기 누적(본문)", {f: v}, v)] if v is not None else []
    v = (d(q) or (None, None))[0]
    return [(f"{q}분기 3개월(본문)", {f: v}, v)] if v is not None else []


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true")
    ap.add_argument("--insert", action="store_true", help="원장 행이 없는 칸은 새 행으로 삽입(그 행의 FnGuide 일치 칸만)")
    ap.add_argument("--replace", action="store_true", help="10-11: '원인 조사' 칸도 — 원문 후보(원본·재작성) = FnGuide면 기존 값 교체(단위 오류 종목 포함, 칸 단위 일치만)"); a = ap.parse_args()
    conn = connect_primary_db(timeout=900, readonly=not a.apply)
    bid = conn.execute("SELECT baseline_id FROM financial_field_baseline ORDER BY created_at DESC LIMIT 1").fetchone()[0]
    base = {tuple(r) for r in conn.execute("SELECT stock_code, year, quarter, report_type, field FROM financial_field_baseline WHERE baseline_id=?", (bid,)).fetchall()}
    unit_keys = "'fs_quirk:reporting_currency'" if a.replace else "'fs_quirk:reporting_currency','fs_quirk:dart_unit_error','fs_quirk:unit_scale_fixed'"
    excl = {r[0] for r in conn.execute(f"""SELECT stock_code FROM stock_collection_config WHERE config_key IN ({unit_keys})
        OR (config_key='fs_quirk:fiscal_period' AND config_value LIKE 'REIT%%') OR (config_key='entity_category' AND config_value='reit')
        UNION SELECT stock_code FROM stock_universe WHERE stock_name LIKE '%%리츠%%'""").fetchall()}
    locks = {(r[0], r[1], r[2]) for r in conn.execute("SELECT stock_code, year, table_name FROM data_lock WHERE is_locked=1").fetchall()}
    stat_cond = "(status LIKE '미확인%%' OR status='원인 조사')" if a.replace else "status LIKE '미확인%%'"
    cells = [tuple(r) for r in conn.execute(f"""SELECT stock_code, year, quarter, report_type, field, fnguide_value FROM financial_field_verification
        WHERE {stat_cond} AND fnguide_value IS NOT NULL AND field IN ({','.join('?' * (len(FIN) + len(CF)))})""", FIN + CF).fetchall()]
    st, plan, norow, hold = collections.Counter(), [], [], []
    try:
        docv = {(r[0], r[1], r[2]): r[3] for r in conn.execute("SELECT stock_code, fiscal_year, field, value FROM ofs_doc_values").fetchall()}
    except Exception:
        conn.rollback(); docv = {}
    try:
        docs = {(r[0], r[1], r[2], r[3], r[4]): (r[5], r[6]) for r in conn.execute(
            "SELECT stock_code, fiscal_year, quarter, report_type, field, value_3m, value_cum FROM doc_statement_values").fetchall()}
    except Exception:
        conn.rollback(); docs = {}
    for (c_, y_, f_), v_ in docv.items():  # 옛 별도 연간 표도 합침(없을 때만)
        docs.setdefault((c_, y_, 0, "OFS", f_), (v_, v_))
    for code, y, q, fs, f, fg in cells:
        if (code, y, q, fs, f) not in base:
            st["기준선 밖(신규 미검증 — 48시간 고정)"] += 1; continue
        if code in excl:
            st["다른 원인군(리츠·외화·단위)"] += 1; continue
        tbl = "cash_flow_data" if f in CF else "financial_data"
        if (code, y, tbl) in locks:
            st["잠금"] += 1; continue
        cs = candidates(code, y, q, fs, f)
        if not cs:  # 10-11: DART 재무 API 값이 없는 칸 → 사업·분기보고서 본문의 같은 구분(연결/별도) 재무제표 표(doc_statement_values) — 산식은 fin_norm
            cs = doc_cands(code, y, q, fs, f, docs)
        if not cs:
            st[f"{fs} 원문 없음"] += 1; continue
        hit = [c for c in cs if abs(c[2] - fg) <= TOL]
        if not hit:
            st[f"{fs} 원문 후보 ≠ FnGuide"] += 1; hold.append((code, y, q, fs, f, fg, [(c[0], c[2]) for c in cs])); continue
        lab, new, _ = hit[0]
        cond = "is_annual" if q == 0 else "NOT is_annual AND quarter=?"
        cols = list(new)
        rows = conn.execute(f"SELECT id, {','.join(cols)} FROM {tbl} WHERE stock_code=? AND year=? AND report_type=? AND {cond}",
                            (code, y, fs) if q == 0 else (code, y, fs, q)).fetchall()
        if not rows:
            st[f"{fs} 원장 행 없음(삽입 단계로)"] += 1; norow.append((tbl, code, y, q, fs, f, new, lab)); continue
        n = 0
        for row in rows:
            for i, c in enumerate(cols):
                old = row[i + 1]
                if old is None or (a.replace and abs(old - new[c]) > 0.5):
                    if old is not None and _ofs.scale_jump(old, new[c]):
                        st["1,000배 차이 — 단위 오류 정정(칸 FnGuide 일치)"] += 1
                    plan.append((tbl, row[0], code, y, q, fs, c, old, new[c], lab)); n += 1
        st[f"{fs} 채움(FnGuide 일치)" if n else f"{fs} 원장 칸이 이미 있음(검증표와 시차)"] += 1
    res = {"at": datetime.now().isoformat(timespec="seconds"), "mode": "apply" if a.apply else "dry_run", "baseline_id": bid,
           "cells": len(cells), "stats": dict(st), "plan_cells": len(plan), "no_row": len(norow), "hold": len(hold)}
    pd.DataFrame(norow, columns=["table", "stock_code", "year", "quarter", "fs", "field", "values", "rule"]).to_csv(OUT / "fill_missing_norow_20261011.csv", index=False)
    pd.DataFrame(hold, columns=["stock_code", "year", "quarter", "fs", "field", "fnguide", "candidates"]).to_csv(OUT / "fill_missing_hold_20261011.csv", index=False)
    ins = collections.defaultdict(dict)  # (tbl, code, y, q, fs) → {칸: 값}
    for tbl, code, y, q, fs, f, new_vals, lab in norow:
        ins[(tbl, code, y, q, fs)].update(new_vals)
    res["insert_rows"] = len(ins) if a.insert else 0
    if a.apply and (plan or (a.insert and ins)):
        run_id = f"fill_raw_{datetime.now():%Y%m%d_%H%M%S}"
        now = datetime.now().isoformat(timespec="seconds")
        if a.insert:
            for (tbl, code, y, q, fs), vals in ins.items():
                conn.execute(f"SELECT setval('{tbl}_id_seq', (SELECT COALESCE(MAX(id),1) FROM {tbl}))")
                cols = list(vals)
                allc = ["stock_code", "year", "quarter", "is_annual", "report_type", "data_source", "created_at", "updated_at"] + cols
                rid = conn.execute(f"INSERT INTO {tbl} ({','.join(allc)}) VALUES ({','.join('?' * len(allc))}) RETURNING id",
                                   [code, y, q, q == 0, fs, TAG.lstrip("+") + "_insert", now, now] + [vals[c] for c in cols]).fetchone()[0]
                for c in cols:
                    plan.append((tbl, rid, code, y, q, fs, c, None, vals[c], "새 행(원장 행 없음) — FnGuide 일치 칸만"))
        for tbl, log in (("financial_data", "financial_fix_log"), ("cash_flow_data", "cashflow_fix_log")):
            ps = [p for p in plan if p[0] == tbl]
            if not ps:
                continue
            bk = f"{tbl}_backup_fill_raw_20261011"
            conn.execute(f"CREATE TABLE IF NOT EXISTS {bk} AS SELECT *, CAST(NULL AS TEXT) run_id FROM {tbl} WHERE false")
            ids = sorted({p[1] for p in ps})
            for i in range(0, len(ids), 1000):
                part = ids[i:i + 1000]
                conn.execute(f"INSERT INTO {bk} SELECT *, ? FROM {tbl} WHERE id IN ({','.join('?' * len(part))})", [run_id] + part)
                conn.execute(f"UPDATE {tbl} SET data_source=COALESCE(data_source,'')||? WHERE id IN ({','.join('?' * len(part))}) AND COALESCE(data_source,'') NOT LIKE ?",
                             [TAG] + part + [f"%{TAG}%"])
            for _, rid, code, y, q, fs, c, old, new, lab in ps:
                conn.execute(f"UPDATE {tbl} SET {c}=?, updated_at=? WHERE id=?" + ("" if a.replace else f" AND {c} IS NULL"), (new, now, rid))
            conn.execute(f"SELECT setval('{log}_id_seq', (SELECT COALESCE(MAX(id),1) FROM {log}))")
            conn.executemany(f"""INSERT INTO {log}(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             [(now, rid, code, y, q, 1 if q == 0 else 0, fs, c, old, new, ("빈 칸 채움" if old is None else "교체") + f" — {lab} = FnGuide", "DART 원문(fin_norm)", run_id)
                              for _, rid, code, y, q, fs, c, old, new, lab in ps])
        conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
        conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)""",
                     (now, "financial_data+cash_flow_data", "원인군 DB 없음(원문 있음) — 고정 분모", len(plan), "fin_norm 후보 = FnGuide(200만원)인 빈 칸만 채움",
                      json.dumps(dict(st), ensure_ascii=False)[:900], "", "scripts/review/fill_missing_from_raw_20261011.py", run_id))
        conn.commit()
        bad = [p for p in plan if conn.execute(f"SELECT {p[6]} FROM {p[0]} WHERE id=?", (p[1],)).fetchone()[0] is None
               or abs(float(conn.execute(f"SELECT {p[6]} FROM {p[0]} WHERE id=?", (p[1],)).fetchone()[0]) - p[8]) > 0.5]
        res.update({"applied": True, "run_id": run_id, "post_readback": {"planned": len(plan), "mismatch": len(bad), "pass": not bad},
                    "canonical_sync": _ofs.canonical_sync(conn, run_id, now, {p[1] for p in plan if p[0] == "financial_data"},
                                                          {p[1] for p in plan if p[0] == "cash_flow_data"})})
        conn.close()
        pr = subprocess.run([sys.executable, str(ROOT / "scripts" / "review" / "build_field_verification_20261003.py")], capture_output=True, text=True, cwd=str(ROOT))
        res["field_verification_after"] = (pr.stdout or pr.stderr).strip().splitlines()[-1:]
    out = OUT / f"fill_missing_from_raw_20261011{'_replace' if a.replace else ''}_{'apply' if a.apply else 'dry'}.json"
    if out.exists() and a.apply:
        out = out.with_name(out.stem + f"_{datetime.now():%H%M%S}.json")
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print(json.dumps({k: res.get(k) for k in ("mode", "stats", "plan_cells", "no_row", "hold", "post_readback", "field_verification_after")}, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
