#!/usr/bin/env python3
"""운영 재무·현금흐름의 단위 배율 오류(1,000배·100만 배) 복원(2026-10-05, FINANCIAL_STATEMENTS.md §9-2-8 #1).

탐지: 같은 종목·같은 구분(CFS/OFS) 행들의 중앙값 대비 300배 이상 / 300분의 1 이하인 칸
      financial_data: total_assets 기준 행 탐지 후 금액 필드 전부 검사, cash_flow_data: operating_cf 기준.
판정(원칙 0 — FnGuide 원문 일치분만 반영): 후보 = 현재값 ÷ k (k ∈ {1e3, 1e6, 1e-3, 1e-6})
  같은 회계 키·같은 구분의 FnGuide wcomp 원문 값과 max(1억, 0.5%) 이내 → 반영. FnGuide가 없거나 안 맞으면 보류(목록).
반영 시: 백업(financial_data_backup_unit_20261005 / cash_flow_data_backup_unit_20261005), financial_fix_log·cashflow_fix_log(필드 단위),
        data_fix_log, 그리고 fs_quirk:unit_scale_fixed(종목) 표시 — apply_dart_refetch_20261002.py 가 같은 칸을 다시 덮지 않게.
외국기업(보고통화)·자산 0 행은 여기서 다루지 않는다(§9-2-8 #5, fix_fx_q2_20261005 처리).
"""
import argparse
import collections
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "review"))
from compare_db_vs_fnguide_raw_20261003 import parse_code  # noqa: E402
from db_compat import connect_primary_db  # noqa: E402

FIN = ["revenue", "operating_profit", "net_income", "total_assets", "total_liabilities", "total_equity"]
CF = ["operating_cf", "investing_cf", "financing_cf", "capex"]
KS = (1e3, 1e6, 1e-3, 1e-6)
OUT = ROOT / "research_outputs" / "financial_rereview_20261002"


def close(a, b):
    return a is not None and b is not None and abs(a - b) <= max(1e8, abs(b) * 0.005)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    conn = connect_primary_db(timeout=900, readonly=not a.apply)
    fx = {r[0] for r in conn.execute("SELECT stock_code FROM stock_collection_config WHERE config_key='fs_quirk:reporting_currency' AND config_value<>'KRW'").fetchall()}
    plan, hold, st = [], [], collections.Counter()
    fgc = {}
    for tbl, key, fields in (("financial_data", "total_assets", FIN), ("cash_flow_data", "operating_cf", CF)):
        rows = [tuple(r) for r in conn.execute(f"""
            WITH m AS (SELECT stock_code, report_type, percentile_cont(0.5) WITHIN GROUP (ORDER BY abs({key})) med
                       FROM {tbl} WHERE {key} IS NOT NULL AND {key}<>0 GROUP BY 1,2)
            SELECT t.id, t.stock_code, t.year, t.quarter, t.is_annual, t.report_type, {','.join('t.' + f for f in fields)}, m.med
            FROM {tbl} t JOIN m USING (stock_code, report_type)
            WHERE t.{key} IS NOT NULL AND t.{key}<>0 AND m.med>0 AND (abs(t.{key})/m.med>=300 OR abs(t.{key})/m.med<=1.0/300)""").fetchall()]
        for r in rows:
            rid, code, y, q, ann, fs = r[:6]
            if code in fx:
                st[f"{tbl}: 외국기업(별도 처리)"] += 1
                continue
            if code not in fgc:
                fgc[code], _ = parse_code(code)
            fg = fgc[code].get((y, 0 if ann else q, fs)) or {}
            vals = dict(zip(fields, r[6:6 + len(fields)]))
            fixed = {}
            for f, v in vals.items():
                if v is None or v == 0:
                    continue
                e = fg.get(f)
                if e is None:
                    continue
                if close(v, e):
                    continue  # 이 칸은 이미 맞음
                k = next((k for k in KS if close(v / k, e)), None)
                if k:
                    fixed[f] = (v, v / k, e, k)
            if fixed:
                # 같은 행의 나머지 금액 칸: FnGuide 값이 없을 때만, 그 필드의 종목 중앙값 대비 배율이 확인된 k와 같은 자릿수(÷10~×10)면 함께 복원
                ks = {x[3] for x in fixed.values()}
                if len(ks) == 1:
                    k = ks.pop()
                    for f, v in vals.items():
                        if f in fixed or not v or fg.get(f) is not None:
                            continue
                        med = conn.execute(f"SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY abs({f})) FROM {tbl} WHERE stock_code=? AND report_type=? AND {f}<>0",
                                           (code, fs)).fetchone()[0]
                        if med and 0.1 <= abs(v) / med / k <= 10:
                            fixed[f] = (v, v / k, None, k)
                st[f"{tbl}: FnGuide 일치 → 복원"] += 1
                plan.append((tbl, rid, code, y, q, ann, fs, fixed))
            else:
                st[f"{tbl}: FnGuide 없음·불일치 → 보류"] += 1
                hold.append((tbl, rid, code, y, q, ann, fs, {f: v for f, v in vals.items() if v}, bool(fg)))
    print(json.dumps(dict(st), ensure_ascii=False, indent=1))
    for p in plan:
        print("복원", p[:7], {f: f"{o:,.0f}→{n:,.0f} (FnGuide {e:,.0f}, ÷{k:g})" if e else f"{o:,.0f}→{n:,.0f} (같은 행 배율 ÷{k:g})" for f, (o, n, e, k) in p[7].items()})
    with open(OUT / "unit_scale_hold_20261005.json", "w") as fh:
        json.dump([list(h[:7]) + [h[7], h[8]] for h in hold], fh, ensure_ascii=False, indent=1, default=str)
    print("보류 목록", OUT / "unit_scale_hold_20261005.json", len(hold))
    if not a.apply or not plan:
        return
    run_id = f"unit_scale_fix_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    for tbl in ("financial_data", "cash_flow_data"):
        conn.execute(f"CREATE TABLE IF NOT EXISTS {tbl}_backup_unit_20261005 AS SELECT *, CAST(NULL AS TEXT) run_id FROM {tbl} WHERE false")
    for tbl in ("financial_fix_log", "cashflow_fix_log"):
        conn.execute(f"SELECT setval('{tbl}_id_seq',(SELECT MAX(id) FROM {tbl}))")
    codes = set()
    n = 0
    for tbl, rid, code, y, q, ann, fs, fixed in plan:
        conn.execute(f"INSERT INTO {tbl}_backup_unit_20261005 SELECT *, ? FROM {tbl} WHERE id=?", (run_id, rid))
        conn.execute(f"UPDATE {tbl} SET {','.join(f + '=?' for f in fixed)}, updated_at=? WHERE id=?", [x[1] for x in fixed.values()] + [now, rid])
        log = "financial_fix_log" if tbl == "financial_data" else "cashflow_fix_log"
        for f, (o, nv, e, k) in fixed.items():
            conn.execute(f"""INSERT INTO {log}(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (now, rid, code, y, q, int(bool(ann)), fs, f, o, nv, f"단위 배율 오류 복원(÷{k:g}), " + ("FnGuide 원문 일치" if e else "같은 행 FnGuide 확인 칸과 같은 배율"), "FnGuide wcomp", run_id))
            n += 1
        codes.add(code)
    for code in codes:
        conn.execute("""INSERT INTO stock_collection_config(stock_code, config_key, config_value) VALUES (?, 'fs_quirk:unit_scale_fixed', ?)
                        ON CONFLICT (stock_code, config_key) DO UPDATE SET config_value=EXCLUDED.config_value""", (code, run_id))
    conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
    conn.execute("INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)",
                 (now, "financial_data+cash_flow_data", "단위 배율 오류 복원", n, "중앙값 대비 300배↑ 칸, ÷k 후보가 FnGuide 원문과 일치할 때만",
                  json.dumps(dict(st), ensure_ascii=False), f"{len(plan)}행 {n}칸, 종목 {len(codes)}", "scripts/review/fix_unit_scale_20261005.py", run_id))
    conn.commit()
    print("적용 완료", run_id, len(plan), "행", n, "칸")


if __name__ == "__main__":
    main()
