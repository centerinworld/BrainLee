#!/usr/bin/env python3
"""원인이 명확한 차이 → FnGuide 기준으로 대체 (2026-10-11, **사용자 지시**: '환율도 큰 범위에서 차이가 나지 않는다면 Fnguide값으로 대체할 것.
원인이 명확한 것은 Fnguide기준으로 할것'). 기본 dry-run. 원칙 0(FnGuide 복사 금지)의 **사용자 승인 예외** — 칸마다 사유 코드로 구분 기록.

모드(고정 분모 ∩ P1 ∩ 미확정 ∩ 리츠 제외):
  --fx         외화 보고통화 종목: DART 원문 × 환산 후보(fix_fx_cells와 같은 후보) 중 하나가 FnGuide와 2% 이내 → FnGuide 값
               (FnGuide 환율 원천이 ECOS 일별과 다름 — 10-11 역산: 950170 FY2024 기말 889.0 vs ECOS 885.76)    reason FNGUIDE_SUBSTITUTED_FX_RATE
  --cf-reclass 현금흐름 '세 활동 합 DB = FnGuide'(FNGUIDE_CF_RECLASS 예외) → 활동 분류를 FnGuide 기준으로       reason FNGUIDE_SUBSTITUTED_CF_RECLASS
  --holding    지주회사 매출 정의(FnGuide = 지분법 손익 등 포함 영업수익): 지정 종목의 매출 칸, DART = DB 확인      reason FNGUIDE_SUBSTITUTED_DEFINITION
대체하지 않음: FNGUIDE_TOTAL_BASIS(FnGuide 전체 순이익 vs 정본 지배 기준 — 사용자 결정 기준 유지), FNGUIDE_QUARTER_INCONSISTENT(FnGuide 내부 불일치),
  FNGUIDE_ONLY_DIFF(원인 불명).
기록: 백업·fix_log(old→FnGuide)·data_source '+fnguide_sub_<mode>'·financial_field_exception(사유 코드, 기존 예외는 덮어씀)·readback·정본·재측정.
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
sys.path.insert(0, str(ROOT / "scripts" / "review"))
from db_compat import connect_primary_db  # noqa: E402
import fin_norm as fn  # noqa: E402

P1 = ("revenue", "operating_profit", "net_income", "total_assets", "total_liabilities", "total_equity", "operating_cf", "investing_cf", "financing_cf")
HOLDING = ("402340", "383800")  # 10-11 확인: SK스퀘어·LX홀딩스 — FnGuide 매출 = 영업수익(지분법 손익 포함)
_spec = importlib.util.spec_from_file_location("apply_ofs_extra", ROOT / "scripts" / "review" / "apply_ofs_extra_20261009.py")
_ofs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ofs)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true")
    ap.add_argument("--mode", required=True, choices=["fx", "cf-reclass", "holding", "no-dart"]); a = ap.parse_args()
    conn = connect_primary_db(timeout=900, readonly=not a.apply)
    bid = conn.execute("SELECT baseline_id FROM financial_field_baseline ORDER BY created_at DESC LIMIT 1").fetchone()[0]
    base = {tuple(r) for r in conn.execute("SELECT stock_code, year, quarter, report_type, field FROM financial_field_baseline WHERE baseline_id=?", (bid,)).fetchall()}
    reit = {r[0] for r in conn.execute("""SELECT stock_code FROM stock_collection_config WHERE (config_key='fs_quirk:fiscal_period' AND config_value LIKE 'REIT%%')
        OR (config_key='entity_category' AND config_value='reit') UNION SELECT stock_code FROM stock_universe WHERE stock_name LIKE '%%리츠%%'""").fetchall()}
    exc = {tuple(r[:5]): r[5] for r in conn.execute("SELECT stock_code, year, quarter, report_type, field, reason_code FROM financial_field_exception").fetchall()}
    cells = [tuple(r) for r in conn.execute("""SELECT stock_code, year, quarter, report_type, field, db_value, fnguide_value, naver_value FROM financial_field_verification
        WHERE status NOT LIKE '확정%%' AND fnguide_value IS NOT NULL""").fetchall()]
    fxm = None
    if a.mode == "fx":
        sp = importlib.util.spec_from_file_location("fxc", ROOT / "scripts" / "review" / "fix_fx_cells_20261011.py")
        fxm = importlib.util.module_from_spec(sp); sp.loader.exec_module(fxm)
        ccy = {}
        for code, v in conn.execute("SELECT stock_code, config_value FROM stock_collection_config WHERE config_key='fs_quirk:reporting_currency'").fetchall():
            c3 = (v or "")[:3]
            if c3 in ("USD", "CNY", "JPY"):
                ccy[code] = "JPY100" if c3 == "JPY" else c3
    reason = {"fx": "FNGUIDE_SUBSTITUTED_FX_RATE", "cf-reclass": "FNGUIDE_SUBSTITUTED_CF_RECLASS", "holding": "FNGUIDE_SUBSTITUTED_DEFINITION",
              "no-dart": "FNGUIDE_SUBSTITUTED_NO_DART"}[a.mode]
    try:
        docs = {tuple(r) for r in conn.execute("SELECT stock_code, fiscal_year, quarter, report_type, field FROM doc_statement_values").fetchall()}
    except Exception:
        conn.rollback(); docs = set()
    fxs = {r[0] for r in conn.execute("SELECT stock_code FROM stock_collection_config WHERE config_key='fs_quirk:reporting_currency'").fetchall()}
    st, plan, reg = collections.Counter(), [], []
    for code, y, q, fs, f, dv, fg, nv in cells:
        k = (code, y, q, fs, f)
        if f not in P1 or k not in base or code in reit:
            continue
        why = None
        if a.mode == "fx":
            if code not in ccy:
                continue
            rc = fn.raw_currency(code, y, 0 if q in (0, 4) else q, fs)
            cc = {"USD": "USD", "CNY": "CNY", "JPY": "JPY100"}.get(rc or "", ccy[code])
            cs = [(lab, v) for lab, v in fxm.cands(code, cc, y, q, fs, f) if v is not None and fg]
            near = [(lab, v, abs(v / fg - 1)) for lab, v in cs if abs(v / fg - 1) <= 0.02]
            if not near:
                st["환산 후보와 2% 넘게 차이 — 대체 안 함"] += 1; continue
            lab, v, d = min(near, key=lambda t: t[2])
            why = f"외화 환산 후보 '{lab}' {v:,.0f}과 FnGuide {fg:,.0f} 차이 {d:.2%}(환율 원천 차이) → FnGuide 값"
        elif a.mode == "cf-reclass":
            if exc.get(k) != "FNGUIDE_CF_RECLASS":
                continue
            why = "현금흐름 세 활동 합 DB = FnGuide(600만원 이내) — FnGuide 활동 분류 기준으로 대체"
        elif a.mode == "holding":
            if code not in HOLDING or f != "revenue":
                continue
            why = "지주회사 매출 정의 — FnGuide 매출 = 영업수익(지분법 손익 등 포함), DART '매출' = DB → FnGuide 정의로 대체"
        else:  # no-dart: DB 없음 ∩ DART 재무 API 원문 없음(013) ∩ 본문 표 값 없음 ∩ 외화 아님
            if dv is not None or code in fxs:
                continue
            qq = 0 if q in (0, 4) else q
            need = [(qq,)] + ([(3,)] if q == 4 and f not in fn.BS else [])
            has_raw = any((fn.RAW_DART / code / "{}_{}_{}.json.gz".format(*fn.dart_key_for_fiscal(code, y, n[0]), fs)).exists() and fn.raw(code, y, n[0], fs, f) is not None for n in need)
            if has_raw or (code, y, q, fs, f) in docs or (code, y, qq, fs, f) in docs:
                st["DART 원문·본문 값 있음 — 대상 아님"] += 1; continue
            why = "DART 재무 API 원문 없음(013)·사업/분기보고서 본문 표에서도 값 없음 → FnGuide 값"
        tbl = "cash_flow_data" if f in fn.FLOW_CF else "financial_data"
        col = f if (q == 0 or tbl == "financial_data") else f + "_q"
        cond = "is_annual" if q == 0 else "NOT is_annual AND quarter=?"
        rows = conn.execute(f"SELECT id, {col} FROM {tbl} WHERE stock_code=? AND year=? AND report_type=? AND {cond}",
                            (code, y, fs) if q == 0 else (code, y, fs, q)).fetchall()
        if not rows:
            if a.mode == "no-dart":
                plan.append((tbl, None, code, y, q, fs, col, None, fg, why)); st["새 행"] += 1
                reg.append(k + (reason, why, dv, fg, nv, "FnGuide 값으로 채움 — DART 원천 없음(원칙 0 사용자 승인 예외)",
                                "사용자 지시 2026-10-11 '원인이 명확한 것은 Fnguide기준'"))
            else:
                st["원장 행 없음"] += 1
            continue
        for rid, old in rows:
            if old is None or abs(old - fg) > 0.5:
                plan.append((tbl, rid, code, y, q, fs, col, old, fg, why))
        st["대체 대상 칸"] += 1
        reg.append(k + (reason, why, dv, fg, nv, "FnGuide 값으로 대체 — 원인 명확(원칙 0 사용자 승인 예외)",
                        "사용자 지시 2026-10-11 '원인이 명확한 것은 Fnguide기준', '환율도 큰 범위에서 차이가 나지 않는다면 Fnguide값으로 대체'"))
    res = {"at": datetime.now().isoformat(timespec="seconds"), "mode": a.mode, "apply": a.apply, "stats": dict(st), "cells": len(reg), "plan_rows": len(plan)}
    if a.apply and plan:
        run_id = f"fnguide_sub_{a.mode.replace('-', '_')}_{datetime.now():%Y%m%d_%H%M%S}"
        now = datetime.now().isoformat(timespec="seconds")
        tag = "+fnguide_sub_" + a.mode.replace("-", "_")
        newrows = collections.defaultdict(dict)
        for p_ in [p_ for p_ in plan if p_[1] is None]:
            newrows[(p_[0], p_[2], p_[3], p_[4], p_[5])][p_[6]] = (p_[8], p_[9])
        plan = [p_ for p_ in plan if p_[1] is not None]
        for (tbl, code, y, q, fs), vals in newrows.items():
            conn.execute(f"SELECT setval('{tbl}_id_seq', (SELECT COALESCE(MAX(id),1) FROM {tbl}))")
            cols = list(vals)
            allc = ["stock_code", "year", "quarter", "is_annual", "report_type", "data_source", "created_at", "updated_at"] + cols
            rid = conn.execute(f"INSERT INTO {tbl} ({','.join(allc)}) VALUES ({','.join('?' * len(allc))}) RETURNING id",
                               [code, y, q, q == 0, fs, tag.lstrip("+"), now, now] + [vals[c][0] for c in cols]).fetchone()[0]
            for c in cols:
                plan.append((tbl, rid, code, y, q, fs, c, None, vals[c][0], vals[c][1] + " (새 행)"))
        for tbl, log in (("financial_data", "financial_fix_log"), ("cash_flow_data", "cashflow_fix_log")):
            ps = [p for p in plan if p[0] == tbl]
            if not ps:
                continue
            bk = f"{tbl}_backup_fnguide_sub_20261011"
            conn.execute(f"CREATE TABLE IF NOT EXISTS {bk} AS SELECT *, CAST(NULL AS TEXT) run_id FROM {tbl} WHERE false")
            ids = sorted({p[1] for p in ps})
            conn.execute(f"INSERT INTO {bk} SELECT *, ? FROM {tbl} WHERE id IN ({','.join('?' * len(ids))})", [run_id] + ids)
            conn.execute(f"UPDATE {tbl} SET data_source=COALESCE(data_source,'')||? WHERE id IN ({','.join('?' * len(ids))}) AND COALESCE(data_source,'') NOT LIKE ?",
                         [tag] + ids + [f"%{tag}%"])
            for _, rid, code, y, q, fs, col, old, new, why in ps:
                conn.execute(f"UPDATE {tbl} SET {col}=?, updated_at=? WHERE id=?", (new, now, rid))
            conn.execute(f"SELECT setval('{log}_id_seq', (SELECT COALESCE(MAX(id),1) FROM {log}))")
            conn.executemany(f"""INSERT INTO {log}(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             [(now, rid, code, y, q, 1 if q == 0 else 0, fs, col, old, new, why, "FnGuide wcomp(사용자 승인 대체)", run_id) for _, rid, code, y, q, fs, col, old, new, why in ps])
        conn.executemany("""INSERT INTO financial_field_exception VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (stock_code, year, quarter, report_type, field) DO UPDATE SET reason_code=EXCLUDED.reason_code, dart_evidence=EXCLUDED.dart_evidence,
            db_value=EXCLUDED.db_value, fnguide_value=EXCLUDED.fnguide_value, exclusion_reason=EXCLUDED.exclusion_reason, approved_basis=EXCLUDED.approved_basis,
            run_id=EXCLUDED.run_id, created_at=EXCLUDED.created_at""", [x + (run_id, now) for x in reg])
        conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
        conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)""",
                     (now, "financial_data+cash_flow_data+financial_field_exception", f"원인 명확 → FnGuide 대체({a.mode})", len(plan), reason,
                      json.dumps(dict(st), ensure_ascii=False), "", "scripts/review/substitute_fnguide_clear_cause_20261011.py", run_id))
        conn.commit()
        bad = [p for p in plan if abs(float(conn.execute(f"SELECT {p[6]} FROM {p[0]} WHERE id=?", (p[1],)).fetchone()[0]) - p[8]) > 0.5]
        res.update({"run_id": run_id, "post_readback": {"planned": len(plan), "mismatch": len(bad), "pass": not bad},
                    "canonical_sync": {k_: v_ for k_, v_ in _ofs.canonical_sync(conn, run_id, now, {p[1] for p in plan if p[0] == "financial_data"},
                                                                              {p[1] for p in plan if p[0] == "cash_flow_data"}).items() if "sample" not in k_}})
        conn.close()
        pr = subprocess.run([sys.executable, str(ROOT / "scripts" / "review" / "build_field_verification_20261003.py")], capture_output=True, text=True, cwd=str(ROOT))
        res["field_verification_after"] = (pr.stdout or pr.stderr).strip().splitlines()[-1:]
    (ROOT / "research_outputs" / "financial_rereview_20261002" / f"fnguide_sub_{a.mode}_20261011_{'apply' if a.apply else 'dry'}.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print(json.dumps(res, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
