#!/usr/bin/env python3
"""원인군 FX — 외화 보고통화 종목 칸 단위 원화 환산 (2026-10-11). 기본 dry-run.

대상: 고정 분모 ∩ P1 필드 ∩ fs_quirk:reporting_currency 종목(통화 미확인 '?' 제외) ∩ 미확정(원인 조사·DB 없음).
원통화 값: DART 원문(fin_norm.raw — 회계 키 → DART 키). 환율: ECOS 731Y001 매매기준율(JPY100 ÷ 100).
후보(칸마다 여러 개, FnGuide와 맞는 것만 채택 — 원칙 0):
  재무상태: 기말 환율 × 그 시점 값
  흐름(손익·현금흐름) 연간: 연간 평균 / 기말 × 연간 값
  흐름 분기 3개월:
    a) 3개월 값 × 그 분기 평균
    b) **누적 차분(가설 10-11)**: 이번 누적 × 누적 기간 평균 − 직전 누적 × 직전 누적 기간 평균  (FnGuide가 누적을 환산한 뒤 차분)
    c) 기말 기준 누적 차분(현금흐름 정본 §2-6 '기말')
  4분기: 연간 × 연간 평균(또는 기말) − 3분기 누적 × 9개월 평균(또는 3분기 기말)
허용: |후보 − FnGuide| ≤ max(200만원, |FnGuide| × 0.1%) (환율 소수·FnGuide 백만원 반올림, P10-3과 같은 기준)
반영: 원장 칸(연간 두 벌 포함) — 값이 다르면 교체, 비면 채움. 행이 없으면 목록만. 백업·fix_log·readback·정본·재측정.
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
import fiscal_period  # noqa: E402

FX = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/ecos_fx/731Y001_daily.json")
OUT = ROOT / "research_outputs" / "financial_rereview_20261002"
P1 = ("revenue", "operating_profit", "net_income", "total_assets", "total_liabilities", "total_equity", "operating_cf", "investing_cf", "financing_cf")
_spec = importlib.util.spec_from_file_location("apply_ofs_extra", ROOT / "scripts" / "review" / "apply_ofs_extra_20261009.py")
_ofs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ofs)
S = {c: pd.Series({pd.Timestamp(t): float(v) for t, v in rows}).sort_index() for c, rows in json.load(open(FX)).items()}


def span(code, fy, q_end, months):
    pe = fiscal_period.period_end(code, fy, q_end)
    end = pd.Timestamp(pe + "-01") + pd.offsets.MonthEnd(0)
    return (end - pd.DateOffset(months=months)) + pd.Timedelta(days=1), end


def fxr(ccy, code, fy, q_end, months, kind):
    a, b = span(code, fy, q_end, months)
    x = S[ccy][a:b] if kind == "avg" else S[ccy][:b]
    if not len(x) or (kind == "end" and (b - x.index[-1]).days > 10):
        return None
    v = float(x.mean()) if kind == "avg" else float(x.iloc[-1])
    return v / 100 if ccy == "JPY100" else v


def mul(v, r):
    return None if v is None or r is None else v * r


def cands(code, ccy, y, q, fs, f):
    out = []
    if f in fn.BS:
        qq = 4 if q in (0, 4) else q
        v = fn.raw(code, y, 0 if q in (0, 4) else q, fs, f)
        out.append(("재무상태 × 기말", mul(v, fxr(ccy, code, y, qq, 3, "end"))))
        return out
    cf = f in fn.FLOW_CF
    if q == 0:
        A = fn.raw(code, y, 0, fs, f)
        out += [("연간 × 연간 평균", mul(A, fxr(ccy, code, y, 4, 12, "avg"))), ("연간 × 기말", mul(A, fxr(ccy, code, y, 4, 12, "end")))]
        return out
    cum = lambda qq: fn.raw(code, y, qq, fs, f) if cf else fn.raw(code, y, qq, fs, f, "thstrm_add_amount")  # noqa: E731
    if q == 4:
        A, c3 = fn.raw(code, y, 0, fs, f), fn.ytd3(code, y, fs, f)
        for kind in ("avg", "end"):
            a, b = mul(A, fxr(ccy, code, y, 4, 12, kind)), mul(c3, fxr(ccy, code, y, 3, 9, kind))
            out.append((f"4분기 = 연간×{kind} − 3분기 누적×{kind}", None if a is None or b is None else a - b))
        return out
    three = fn.raw(code, y, q, fs, f) if not cf else fn.cf_three_month(cum(q), cum(q - 1) if q > 1 else None, q)
    # 10-11 발견: FnGuide 분기 3개월 = 3개월 원통화 × **회계연도 시작~그 분기 말 누적 평균**(12월 결산 2025Q3 USD 1,412.79·CNY 195.52 정확히 일치)
    out.append(("3개월 × 회계 누적 평균", mul(three, fxr(ccy, code, y, q, 3 * q, "avg"))))
    out.append(("3개월 × 분기 평균", mul(three, fxr(ccy, code, y, q, 3, "avg"))))
    for kind in ("avg", "end"):
        a = mul(cum(q), fxr(ccy, code, y, q, 3 * q, kind))
        b = 0.0 if q == 1 else mul(cum(q - 1), fxr(ccy, code, y, q - 1, 3 * (q - 1), kind))
        out.append((f"누적×누적{kind} 차분", None if a is None or b is None else a - b))
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true")
    ap.add_argument("--insert", action="store_true", help="원장 행이 없는 칸은 새 행으로(그 행의 FnGuide 일치 칸만)"); a = ap.parse_args()
    conn = connect_primary_db(timeout=900, readonly=not a.apply)
    bid = conn.execute("SELECT baseline_id FROM financial_field_baseline ORDER BY created_at DESC LIMIT 1").fetchone()[0]
    base = {tuple(r) for r in conn.execute("SELECT stock_code, year, quarter, report_type, field FROM financial_field_baseline WHERE baseline_id=?", (bid,)).fetchall()}
    ccy = {}
    for code, v in conn.execute("SELECT stock_code, config_value FROM stock_collection_config WHERE config_key='fs_quirk:reporting_currency'").fetchall():
        c3 = (v or "")[:3]
        if c3 in ("USD", "CNY", "JPY"):
            ccy[code] = "JPY100" if c3 == "JPY" else c3
    cells = [tuple(r) for r in conn.execute(f"""SELECT stock_code, year, quarter, report_type, field, db_value, fnguide_value FROM financial_field_verification
        WHERE status NOT LIKE '확정%%' AND fnguide_value IS NOT NULL AND stock_code IN ({','.join('?' * len(ccy))})""", list(ccy)).fetchall()]
    st, plan, hold, by_rule = collections.Counter(), [], [], collections.Counter()
    ins = collections.defaultdict(dict)
    for code, y, q, fs, f, dv, fg in cells:
        if f not in P1 or (code, y, q, fs, f) not in base:
            continue
        rc = fn.raw_currency(code, y, 0 if q in (0, 4) else q, fs)  # 10-11: 원문 통화 우선(연도마다 바뀜)
        cc = {"USD": "USD", "CNY": "CNY", "JPY": "JPY100"}.get(rc or "", ccy[code])
        if rc == "KRW":
            st["원문이 이미 원화"] += 1; continue
        cs = [(lab, v) for lab, v in cands(code, cc, y, q, fs, f) if v is not None]
        if not cs:
            st["원문·환율 없음"] += 1; continue
        hit = [(lab, v) for lab, v in cs if abs(v - fg) <= max(2e6, abs(fg) * 0.001)]
        if not hit:
            st["환산 후보 ≠ FnGuide"] += 1; hold.append((code, y, q, fs, f, dv, fg, [(l, round(v)) for l, v in cs])); continue
        lab, newv = hit[0]
        by_rule[lab] += 1
        tbl = "cash_flow_data" if f in fn.FLOW_CF else "financial_data"
        col = f if (q == 0 or tbl == "financial_data") else f + "_q"
        cond = "is_annual" if q == 0 else "NOT is_annual AND quarter=?"
        rows = conn.execute(f"SELECT id, {col} FROM {tbl} WHERE stock_code=? AND year=? AND report_type=? AND {cond}",
                            (code, y, fs) if q == 0 else (code, y, fs, q)).fetchall()
        if not rows:
            st["원장 행 없음"] += 1
            ins[(tbl, code, y, q, fs)][col] = (newv, f"외화 환산: {lab} = FnGuide")
            continue
        n = 0
        for rid, old in rows:
            if old is not None and abs(old - newv) <= 0.5:
                continue
            plan.append((tbl, rid, code, y, q, fs, col, old, newv, f"외화 환산: {lab} = FnGuide")); n += 1
        st["정정(환산 = FnGuide)" if n else "이미 같음"] += 1
    res = {"at": datetime.now().isoformat(timespec="seconds"), "mode": "apply" if a.apply else "dry_run", "stats": dict(st), "by_rule": dict(by_rule),
           "plan_cells": len(plan), "hold": len(hold)}
    pd.DataFrame(hold, columns=["stock_code", "year", "quarter", "fs", "field", "db", "fnguide", "candidates"]).to_csv(OUT / "fx_cells_hold_20261011.csv", index=False)
    res["insert_rows"] = len(ins) if a.insert else 0
    if a.apply and (plan or (a.insert and ins)):
        run_id = f"fx_cells_{datetime.now():%Y%m%d_%H%M%S}"
        now = datetime.now().isoformat(timespec="seconds")
        if a.insert:
            for (tbl, code, y, q, fs), vals in ins.items():
                conn.execute(f"SELECT setval('{tbl}_id_seq', (SELECT COALESCE(MAX(id),1) FROM {tbl}))")
                cols = list(vals)
                allc = ["stock_code", "year", "quarter", "is_annual", "report_type", "data_source", "created_at", "updated_at"] + cols
                rid = conn.execute(f"INSERT INTO {tbl} ({','.join(allc)}) VALUES ({','.join('?' * len(allc))}) RETURNING id",
                                   [code, y, q, q == 0, fs, "fx_cells_20261011_insert", now, now] + [vals[c][0] for c in cols]).fetchone()[0]
                for c in cols:
                    plan.append((tbl, rid, code, y, q, fs, c, None, vals[c][0], vals[c][1] + " (새 행)"))
        for tbl, log in (("financial_data", "financial_fix_log"), ("cash_flow_data", "cashflow_fix_log")):
            ps = [p for p in plan if p[0] == tbl]
            if not ps:
                continue
            bk = f"{tbl}_backup_fx_cells_20261011"
            conn.execute(f"CREATE TABLE IF NOT EXISTS {bk} AS SELECT *, CAST(NULL AS TEXT) run_id FROM {tbl} WHERE false")
            ids = sorted({p[1] for p in ps})
            conn.execute(f"INSERT INTO {bk} SELECT *, ? FROM {tbl} WHERE id IN ({','.join('?' * len(ids))})", [run_id] + ids)  # 새 행은 삽입 직후 값 그대로 백업됨
            for _, rid, code, y, q, fs, col, old, new, lab in ps:
                conn.execute(f"UPDATE {tbl} SET {col}=?, updated_at=? WHERE id=?", (new, now, rid))
            conn.execute(f"SELECT setval('{log}_id_seq', (SELECT COALESCE(MAX(id),1) FROM {log}))")
            conn.executemany(f"""INSERT INTO {log}(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             [(now, rid, code, y, q, 1 if q == 0 else 0, fs, col, old, new, lab, "DART 원문 × ECOS 731Y001", run_id) for _, rid, code, y, q, fs, col, old, new, lab in ps])
        conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
        conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)""",
                     (now, "financial_data+cash_flow_data", "원인군 FX(외화 종목) 칸 단위 환산 — 고정 분모", len(plan), "환산 후보(평균·기말·누적 차분) 중 FnGuide 일치만",
                      json.dumps(dict(st), ensure_ascii=False), json.dumps(dict(by_rule), ensure_ascii=False), "scripts/review/fix_fx_cells_20261011.py", run_id))
        conn.commit()
        bad = [p for p in plan if abs(float(conn.execute(f"SELECT {p[6]} FROM {p[0]} WHERE id=?", (p[1],)).fetchone()[0]) - p[8]) > 0.5]
        res.update({"applied": True, "run_id": run_id, "post_readback": {"planned": len(plan), "mismatch": len(bad), "pass": not bad},
                    "canonical_sync": _ofs.canonical_sync(conn, run_id, now, {p[1] for p in plan if p[0] == "financial_data"},
                                                          {p[1] for p in plan if p[0] == "cash_flow_data"})})
        conn.close()
        pr = subprocess.run([sys.executable, str(ROOT / "scripts" / "review" / "build_field_verification_20261003.py")], capture_output=True, text=True, cwd=str(ROOT))
        res["field_verification_after"] = (pr.stdout or pr.stderr).strip().splitlines()[-1:]
    out = OUT / f"fx_cells_20261011_{'apply' if a.apply else 'dry'}.json"
    if out.exists() and a.apply:
        out = out.with_name(out.stem + f"_{datetime.now():%H%M%S}.json")
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print(json.dumps({k: res.get(k) for k in ("mode", "stats", "by_rule", "plan_cells", "hold", "post_readback", "field_verification_after")}, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
