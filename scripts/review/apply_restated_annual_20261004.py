#!/usr/bin/env python3
"""연간 재작성값(정정값) 반영 — FINANCIAL_STATEMENTS.md §2-5(표시값 = 최신 재작성값, 최초값 보존), 2026-10-04.

재작성값 원문: Y+1년 사업보고서의 전기(frmtrm) 칸(`vals_prev`, fetch_dart_cashflow_20261002.py).
원칙 0(DART 파싱값 불신): **외부(FnGuide 원문·wcomp 캡처, 네이버)가 재작성값과 일치하는 칸만 반영**한다.
  외부 없음/외부가 현재 DB와 일치/외부가 둘 다와 다름 → 반영하지 않고 후보 파일로만 남긴다.
최초값은 financial_facts_pit(as_reported)와 백업·fix_log(old_value)에 보존된다.
대상 칸: financial_data 연간 revenue·operating_profit·net_income(연결=지배, 별도=전체)·total_assets·total_liabilities·total_equity(연결=지배, 별도=자본총계),
         cash_flow_data 연간 operating_cf·investing_cf·financing_cf·capex. 보고통화 종목·data_lock 잠금은 제외.
허용오차 max(200만원, 0.5%). 기본 dry-run.
"""
import argparse
import collections
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "review"))
from compare_db_vs_fnguide_raw_20261003 import RAW, parse_code  # noqa: E402
from db_compat import connect_primary_db  # noqa: E402

SRC = ROOT / "research_outputs" / "financial_rereview_20261002"
FD = {"revenue": "revenue", "operating_profit": "operating_profit", "total_assets": "total_assets", "total_liabilities": "total_liabilities"}
CF = {"operating_cf": "ocf", "investing_cf": "icf", "financing_cf": "fcf", "capex": "capex"}


def close(a, b):
    return a is not None and b is not None and abs(a - b) <= max(2e6, abs(b) * 0.005)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    prev = {}
    for fn in ("dart_cf_2016_2022.jsonl", "dart_cf_full.jsonl"):
        p = SRC / fn
        if not p.exists():
            continue
        for line in open(p):
            d = json.loads(line)
            if d.get("q") == 0 and d.get("fs") and d.get("vals_prev"):
                prev[(d["code"], d["year"] - 1, d["fs"])] = d["vals_prev"]
    conn = connect_primary_db(timeout=900, readonly=not a.apply)
    fx = {r[0] for r in conn.execute("SELECT stock_code FROM stock_collection_config WHERE config_key='fs_quirk:reporting_currency'").fetchall()}
    locks = {(r[0], r[1], r[2]) for r in conn.execute("SELECT stock_code, year, table_name FROM data_lock WHERE is_locked=1").fetchall()}
    # 외부 값: FnGuide 원문 → wcomp 캡처 → 네이버(연결 매출·영업이익)
    ext = {}
    for p in (RAW.iterdir() if RAW.exists() else []):
        if p.is_dir():
            r, _ = parse_code(p.name)
            for (y, q, fs), v in r.items():
                if q == 0:
                    ext[(p.name, y, fs)] = v
    for r in conn.execute("""SELECT DISTINCT ON (stock_code, year, report_type) stock_code, year, report_type, revenue, operating_profit, total_assets,
                                    total_liabilities, operating_cf, investing_cf, financing_cf FROM financial_source_snapshot
                             WHERE data_source='fnguide' AND is_annual=1 AND source_url LIKE 'https://wcomp%' ORDER BY stock_code, year, report_type, fetched_at DESC""").fetchall():
        r = tuple(r)
        k = (r[0], r[1], r[2])
        if k not in ext:
            ext[k] = {f: v for f, v in zip(["revenue", "operating_profit", "total_assets", "total_liabilities", "operating_cf", "investing_cf", "financing_cf"], r[3:])
                      if v is not None}
    nv = {(r[0], r[1]): {"revenue": r[2], "operating_profit": r[3]} for r in map(tuple, conn.execute(
        "SELECT stock_code, year, revenue, operating_profit FROM naver_financial WHERE is_annual=1").fetchall())}
    plan, st, cand = [], collections.Counter(), []
    for tbl, cols in (("financial_data", list(FD) + ["net_income", "total_equity"]), ("cash_flow_data", list(CF))):
        for row in conn.execute(f"SELECT id, stock_code, year, report_type, {','.join(cols)} FROM {tbl} WHERE is_annual AND year>=2016").fetchall():
            row = tuple(row)
            rid, code, y, fs = row[:4]
            pv = prev.get((code, y, fs))
            if not pv or code in fx or (code, y, tbl) in locks:
                continue
            for f, dbv in zip(cols, row[4:]):
                if f in FD:
                    k = FD[f]
                elif f in CF:
                    k = CF[f]
                elif f == "net_income":
                    k = "ni_parent" if fs == "CFS" else "ni_total"
                else:
                    k = "equity_parent" if fs == "CFS" else "equity_total"
                rv = pv.get(k)
                if rv is None or dbv is None:
                    continue
                if f == "capex":
                    rv, dbv = abs(rv), abs(dbv)
                if close(dbv, rv):
                    continue  # 재작성 없음(또는 이미 반영)
                st["재작성 차이 칸"] += 1
                e = (ext.get((code, y, fs)) or {}).get(f)
                e = abs(e) if (e is not None and f == "capex") else e
                n = (nv.get((code, y)) or {}).get(f) if fs == "CFS" else None
                if (e is not None and close(e, rv)) or (e is None and n is not None and close(n, rv)):
                    st["외부가 재작성값 확인 → 반영"] += 1
                    plan.append((tbl, rid, code, y, fs, f, row[4 + cols.index(f)], rv if f != "capex" else -abs(rv) if row[4 + cols.index(f)] < 0 else rv,
                                 "FnGuide" if e is not None else "네이버"))
                elif e is not None and close(e, dbv):
                    st["외부가 현재 DB와 일치 → 보류(재작성 추출 의심)"] += 1
                    cand.append((tbl, code, y, fs, f, dbv, rv, e, "외부=DB"))
                elif e is None and n is None:
                    st["외부 값 없음 → 보류"] += 1
                    cand.append((tbl, code, y, fs, f, dbv, rv, None, "외부 없음"))
                else:
                    st["외부가 둘 다와 다름 → 보류"] += 1
                    cand.append((tbl, code, y, fs, f, dbv, rv, e if e is not None else n, "셋 다 다름"))
    print(json.dumps(dict(st), ensure_ascii=False, indent=1))
    pd.DataFrame(cand, columns=["table", "stock_code", "year", "fs", "field", "db", "restated", "external", "reason"]).to_csv(
        SRC / "restated_annual_hold_candidates.csv", index=False)
    pd.DataFrame(plan, columns=["table", "id", "stock_code", "year", "fs", "field", "old", "new", "confirmed_by"]).to_csv(
        SRC / "restated_annual_apply_plan.csv", index=False)
    if not a.apply or not plan:
        return
    run_id = f"restated_annual_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    for tbl, log in (("financial_data", "financial_fix_log"), ("cash_flow_data", "cashflow_fix_log")):
        rows = [p for p in plan if p[0] == tbl]
        if not rows:
            continue
        conn.execute(f"CREATE TABLE IF NOT EXISTS {tbl}_backup_restated_20261004 AS SELECT *, CAST(NULL AS TEXT) run_id FROM {tbl} WHERE false")
        ids = sorted({p[1] for p in rows})
        for i in range(0, len(ids), 1000):
            ch = ids[i:i + 1000]
            conn.execute(f"INSERT INTO {tbl}_backup_restated_20261004 SELECT *, ? FROM {tbl} WHERE id IN ({','.join('?' * len(ch))})", [run_id] + ch)
        for _, rid, code, y, fs, f, old, new, by in rows:
            conn.execute(f"UPDATE {tbl} SET {f}=?, updated_at=? WHERE id=?", (new, now, rid))
        conn.execute(f"SELECT setval('{log}_id_seq', (SELECT COALESCE(MAX(id),1) FROM {log}))")
        conn.executemany(f"""INSERT INTO {log}(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                             VALUES (?,?,?,?,0,1,?,?,?,?,?,?,?)""",
                         [(now, rid, code, y, fs, f, old, new, f"재작성값 반영(§2-5) — {by} 확인", "DART 다음연도 사업보고서 전기 칸", run_id)
                          for _, rid, code, y, fs, f, old, new, by in rows])
    conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
    conn.execute("INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)",
                 (now, "financial_data+cash_flow_data", "연간 재작성값(외부 확인분만)", len(plan), "표시값=최신 재작성값(§2-5), 최초값은 PIT·백업 보존",
                  json.dumps(dict(st), ensure_ascii=False), "DART 전기 칸 + FnGuide/네이버 일치", "scripts/review/apply_restated_annual_20261004.py", run_id))
    conn.commit()
    print("적용 완료", run_id, len(plan))


if __name__ == "__main__":
    main()
