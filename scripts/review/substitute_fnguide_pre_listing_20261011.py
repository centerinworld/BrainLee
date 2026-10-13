#!/usr/bin/env python3
"""원인군 SPECIAL(상장 전 연도 — DART 사업보고서 없음) → FnGuide 값으로 대체 (2026-10-11, **사용자 명시 승인: '특수 값은 FnGuide 값으로 대체하고 확정해'**). 기본 dry-run.

원칙 0(FnGuide 값 복사 금지)의 **예외**: 그 연도 DART 원문이 없음을 확인한 칸만(fnlttSinglAcntAll 013, 다음 해 사업보고서 전기 칸도 없음).
대상: 고정 분모 ∩ P1 필드 ∩ '원인 조사' ∩ 리츠 제외 ∩ 연간(quarter 0) ∩ 그 해 원문 파일 없음 ∩ 다음 해 전기 칸 없음.
기록(구분 필수 — DART 확인 칸과 섞지 않는다): data_source '+fnguide_pre_listing_sub', fix_log rule '상장 전 연도 FnGuide 대체(DART 원천 없음, 사용자 승인 10-11)',
  financial_field_exception reason_code='FNGUIDE_SUBSTITUTED_PRE_LISTING'(확정 집계는 DB = FnGuide라 '확정'으로 잡히지만, 이 표로 'DART 미확인 확정'을 따로 셀 수 있게).
"""
from __future__ import annotations

import argparse
import re
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
TAG = "+fnguide_pre_listing_sub"
_spec = importlib.util.spec_from_file_location("apply_ofs_extra", ROOT / "scripts" / "review" / "apply_ofs_extra_20261009.py")
_ofs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ofs)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true")
    ap.add_argument("--missing", action="store_true", help="10-11: DB 없음 칸 — 상장일보다 앞선 기간(연간·분기)·DART 원문·본문 없음이면 FnGuide 값으로 채움/새 행")
    a = ap.parse_args()
    conn = connect_primary_db(timeout=900, readonly=not a.apply)
    bid = conn.execute("SELECT baseline_id FROM financial_field_baseline ORDER BY created_at DESC LIMIT 1").fetchone()[0]
    base = {tuple(r) for r in conn.execute("SELECT stock_code, year, quarter, report_type, field FROM financial_field_baseline WHERE baseline_id=?", (bid,)).fetchall()}
    reit = {r[0] for r in conn.execute("""SELECT stock_code FROM stock_collection_config WHERE (config_key='fs_quirk:fiscal_period' AND config_value LIKE 'REIT%%')
        OR (config_key='entity_category' AND config_value='reit') UNION SELECT stock_code FROM stock_universe WHERE stock_name LIKE '%%리츠%%'""").fetchall()}
    fx = {r[0] for r in conn.execute("SELECT stock_code FROM stock_collection_config WHERE config_key='fs_quirk:reporting_currency'").fetchall()}
    if a.missing:
        cells = [tuple(r) for r in conn.execute("""SELECT stock_code, year, quarter, report_type, field, db_value, fnguide_value, naver_value FROM financial_field_verification
            WHERE (status LIKE '미확인%%' OR status='원인 조사') AND fnguide_value IS NOT NULL""").fetchall()]  # 10-11: 상장 전 분기 원인 조사 칸 포함
    else:
        cells = [tuple(r) for r in conn.execute("""SELECT stock_code, year, quarter, report_type, field, db_value, fnguide_value, naver_value FROM financial_field_verification
            WHERE status='원인 조사' AND quarter=0 AND fnguide_value IS NOT NULL""").fetchall()]
    import fiscal_period
    listed = {r[0]: str(r[1] or "") for r in conn.execute("SELECT stock_code, listed_date FROM stock_universe").fetchall()}
    try:
        docv = {(r[0], r[1], r[2]) for r in conn.execute("SELECT stock_code, fiscal_year, field FROM ofs_doc_values").fetchall()}
        docq = {(r[0], r[1], r[2], r[3]) for r in conn.execute("SELECT stock_code, fiscal_year, quarter, field FROM ofs_doc_values_q").fetchall()}
    except Exception:
        conn.rollback(); docv, docq = set(), set()
    ins = collections.defaultdict(dict)
    st, plan, reg = collections.Counter(), [], []
    for code, y, q, fs, f, dv, fg, nv in cells:
        if f not in P1 or (code, y, q, fs, f) not in base or code in reit or code in fx:
            continue
        if a.missing:
            pe = fiscal_period.period_end(code, y, 4 if q in (0, 4) else q).replace("-", "")  # 'YYYYMM'
            ld = re.sub(r"\D", "", listed.get(code, ""))[:6]
            if not ld or pe >= ld:
                continue  # 상장 이후 기간 — 대상 아님
            qq = 0 if q in (0, 4) else q
            by, bq = fn.dart_key_for_fiscal(code, y, qq)
            if (fn.RAW_DART / code / f"{by}_{bq}_{fs}.json.gz").exists() or (code, y, f) in docv or (code, y, q, f) in docq:
                continue  # DART 원문·본문 값이 있으면 대상 아님
        elif (fn.RAW_DART / code / f"{y}_0_{fs}.json.gz").exists() or fn.raw(code, y + 1, 0, fs, f, "frmtrm_amount") is not None:
            continue  # DART 원천이 있으면 대상 아님(다른 원인군)
        tbl = "cash_flow_data" if f in fn.FLOW_CF else "financial_data"
        col = f if (q == 0 or tbl == "financial_data") else f + "_q"
        cond = "is_annual" if q == 0 else "NOT is_annual AND quarter=?"
        rows = conn.execute(f"SELECT id, {col} FROM {tbl} WHERE stock_code=? AND year=? AND report_type=? AND {cond}",
                            (code, y, fs) if q == 0 else (code, y, fs, q)).fetchall()
        if not rows:
            if a.missing:
                ins[(tbl, code, y, q, fs)][col] = fg
                st["새 행(상장 전)"] += 1
                reg.append((code, y, q, fs, f, "FNGUIDE_SUBSTITUTED_PRE_LISTING",
                            "상장 전 기간(상장일 > 기간 종료) — DART 원문·본문 없음, FnGuide는 증권신고서 기반", dv, fg, nv,
                            "FnGuide 값으로 채움 — DART 미확인 확정(원칙 0 예외)", "사용자 승인 2026-10-11 '특수 값은 FnGuide 값으로 대체하고 확정해'(같은 원인 — 상장 전 연도)"))
            else:
                st["원장 행 없음"] += 1
            continue
        for rid, old in rows:
            if old is None or abs(old - fg) > 0.5:
                plan.append((tbl, rid, code, y, fs, col, old, fg))
        st["대체 대상 칸"] += 1
        reg.append((code, y, q, fs, f, "FNGUIDE_SUBSTITUTED_PRE_LISTING",
                    "그 해 DART 사업보고서 없음(fnlttSinglAcntAll 013, 다음 해 전기 칸 없음) — 상장 전 연도, FnGuide는 증권신고서 기반",
                    dv, fg, nv, "FnGuide 값으로 대체 — DART 미확인 확정(원칙 0 예외)", "사용자 승인 2026-10-11 '특수 값은 FnGuide 값으로 대체하고 확정해'"))
    res = {"at": datetime.now().isoformat(timespec="seconds"), "mode": "apply" if a.apply else "dry_run", "stats": dict(st),
           "plan_rows": len(plan), "cells": len(reg), "stocks": len({r[0] for r in reg})}
    res["insert_rows"] = len(ins)
    if a.apply and (plan or ins):
        run_id = f"fnguide_pre_listing_{datetime.now():%Y%m%d_%H%M%S}"
        now = datetime.now().isoformat(timespec="seconds")
        for (tbl, code, y, q, fs), vals in ins.items():
            conn.execute(f"SELECT setval('{tbl}_id_seq', (SELECT COALESCE(MAX(id),1) FROM {tbl}))")
            cols = list(vals)
            allc = ["stock_code", "year", "quarter", "is_annual", "report_type", "data_source", "created_at", "updated_at"] + cols
            rid = conn.execute(f"INSERT INTO {tbl} ({','.join(allc)}) VALUES ({','.join('?' * len(allc))}) RETURNING id",
                               [code, y, q, q == 0, fs, TAG.lstrip("+"), now, now] + [vals[c] for c in cols]).fetchone()[0]
            for c in cols:
                plan.append((tbl, rid, code, y, fs, c, None, vals[c]))
        for tbl, log in (("financial_data", "financial_fix_log"), ("cash_flow_data", "cashflow_fix_log")):
            ps = [p for p in plan if p[0] == tbl]
            if not ps:
                continue
            bk = f"{tbl}_backup_fnguide_pre_listing_20261011"
            conn.execute(f"CREATE TABLE IF NOT EXISTS {bk} AS SELECT *, CAST(NULL AS TEXT) run_id FROM {tbl} WHERE false")
            ids = sorted({p[1] for p in ps})
            conn.execute(f"INSERT INTO {bk} SELECT *, ? FROM {tbl} WHERE id IN ({','.join('?' * len(ids))})", [run_id] + ids)
            conn.execute(f"UPDATE {tbl} SET data_source=COALESCE(data_source,'')||? WHERE id IN ({','.join('?' * len(ids))}) AND COALESCE(data_source,'') NOT LIKE ?",
                         [TAG] + ids + [f"%{TAG}%"])
            for _, rid, code, y, fs, f, old, new in ps:
                conn.execute(f"UPDATE {tbl} SET {f}=?, updated_at=? WHERE id=?", (new, now, rid))
            conn.execute(f"SELECT setval('{log}_id_seq', (SELECT COALESCE(MAX(id),1) FROM {log}))")
            qmap = {r[0]: (r[1], r[2]) for r in conn.execute(f"SELECT id, quarter, is_annual FROM {tbl} WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()}
            conn.executemany(f"""INSERT INTO {log}(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             [(now, rid, code, y, qmap[rid][0], 1 if qmap[rid][1] else 0, fs, f, old, new, "상장 전 기간 FnGuide 대체(DART 원천 없음, 사용자 승인 10-11)", "FnGuide wcomp 원문", run_id)
                              for _, rid, code, y, fs, f, old, new in ps])
        conn.executemany("""INSERT INTO financial_field_exception VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (stock_code, year, quarter, report_type, field) DO UPDATE SET reason_code=EXCLUDED.reason_code, dart_evidence=EXCLUDED.dart_evidence,
            db_value=EXCLUDED.db_value, fnguide_value=EXCLUDED.fnguide_value, exclusion_reason=EXCLUDED.exclusion_reason, approved_basis=EXCLUDED.approved_basis,
            run_id=EXCLUDED.run_id, created_at=EXCLUDED.created_at""", [x + (run_id, now) for x in reg])
        conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
        conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)""",
                     (now, "financial_data+cash_flow_data+financial_field_exception", "상장 전 연도(DART 원천 없음) FnGuide 대체", len(plan),
                      "원칙 0 예외 — 사용자 승인 10-11, reason FNGUIDE_SUBSTITUTED_PRE_LISTING", json.dumps(dict(st), ensure_ascii=False), "",
                      "scripts/review/substitute_fnguide_pre_listing_20261011.py", run_id))
        conn.commit()
        bad = [p for p in plan if abs(float(conn.execute(f"SELECT {p[5]} FROM {p[0]} WHERE id=?", (p[1],)).fetchone()[0]) - p[7]) > 0.5]
        res.update({"applied": True, "run_id": run_id, "post_readback": {"planned": len(plan), "mismatch": len(bad), "pass": not bad},
                    "canonical_sync": _ofs.canonical_sync(conn, run_id, now, {p[1] for p in plan if p[0] == "financial_data"},
                                                          {p[1] for p in plan if p[0] == "cash_flow_data"})})
        conn.close()
        pr = subprocess.run([sys.executable, str(ROOT / "scripts" / "review" / "build_field_verification_20261003.py")], capture_output=True, text=True, cwd=str(ROOT))
        res["field_verification_after"] = (pr.stdout or pr.stderr).strip().splitlines()[-1:]
    out = ROOT / "research_outputs" / "financial_rereview_20261002" / f"fnguide_pre_listing_20261011{'_missing' if a.missing else ''}_{'apply' if a.apply else 'dry'}.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print(json.dumps({k: res.get(k) for k in ("mode", "stats", "plan_rows", "insert_rows", "cells", "stocks", "post_readback", "field_verification_after")}, ensure_ascii=False, indent=1, default=str))
    if res.get("canonical_sync"):
        print({k: v for k, v in res["canonical_sync"].items() if "sample" not in k})


if __name__ == "__main__":
    main()
