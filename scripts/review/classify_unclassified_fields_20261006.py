#!/usr/bin/env python3
"""필드 검증 '미분류' 칸을 DART 원문과 3자 비교로 분류(2026-10-06, docs/OPEN_ITEMS.md C3).

대상: financial_field_verification status='원인 조사' AND cause='미분류'.
비교값: DB, FnGuide, DART 원문 당기(vals), DART 다음 해 사업보고서 전기(vals_prev = 재작성값, 연간만).
분류:
  db_error        DART 원문 = FnGuide ≠ DB → DB가 틀림(원칙 0 충족: DART 후보가 FnGuide로 확정) — --apply 시 DART 값으로 수정
  restated        DART 재작성값(다음 해 전기) = FnGuide → 재작성값 미반영(§2-5 규칙으로 처리할 대상)
  definition      DART 원문 = DB ≠ FnGuide → FnGuide 정의 차이(우리 저장은 원문과 같음)
  all_differ      셋 다 다름
  no_dart         원문 없음(4분기 파생 포함)
결과: research_outputs/financial_rereview_20261002/unclassified_fields_20261006.csv
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
from db_compat import connect_primary_db  # noqa: E402

SRC = ROOT / "research_outputs" / "financial_rereview_20261002"
KEY = {"revenue": "revenue", "operating_profit": "operating_profit", "net_income": "ni_parent", "total_assets": "total_assets",
       "total_liabilities": "total_liabilities", "total_equity": "equity_parent", "operating_cf": "ocf", "investing_cf": "icf",
       "financing_cf": "fcf", "capex": "capex"}
ALT = {"net_income": "ni_total", "total_equity": "equity_total"}


def close(a, b):
    return a is not None and b is not None and abs(a - b) <= max(1e8, abs(b) * 0.005)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    dart = {}
    for fn in ("dart_cf_2016_2022.jsonl", "dart_cf_full.jsonl"):
        p = SRC / fn
        if p.exists():
            for line in open(p):
                d = json.loads(line)
                if d.get("ok") and d.get("fs"):
                    dart[(d["code"], d["year"], d["q"], d["fs"])] = d
    conn = connect_primary_db(timeout=600, readonly=not a.apply)
    fmap = {r[0]: r[1] for r in conn.execute("SELECT stock_code, config_value FROM stock_collection_config WHERE config_key='fs_quirk:fiscal_month'").fetchall()}
    rows = [tuple(r) for r in conn.execute("""SELECT stock_code, year, quarter, report_type, field, db_value, fnguide_value
                                              FROM financial_field_verification WHERE status='원인 조사' AND COALESCE(cause,'')='미분류'""").fetchall()]
    out, st = [], collections.Counter()
    for code, y, q, fs, f, db, fg in rows:
        if fmap.get(code, "12") != "12" or q == 4:
            out.append((code, y, q, fs, f, db, fg, None, None, "no_dart")); st["no_dart"] += 1
            continue
        d = dart.get((code, y, q, fs)) or {}
        k = KEY.get(f)
        dv = (d.get("vals") or {}).get(k) if k else None
        if dv is None and f in ALT:
            dv = (d.get("vals") or {}).get(ALT[f])
        nxt = dart.get((code, y + 1, 0, fs)) or {}
        rv = (nxt.get("vals_prev") or {}).get(k) if (q == 0 and k) else None
        if dv is None:
            kind = "no_dart"
        elif close(dv, fg) and not close(db, fg):
            kind = "db_error"
        elif rv is not None and close(rv, fg):
            kind = "restated"
        elif close(dv, db):
            kind = "definition"
        else:
            kind = "all_differ"
        st[kind] += 1
        out.append((code, y, q, fs, f, db, fg, dv, rv, kind))
    df = pd.DataFrame(out, columns=["code", "year", "quarter", "fs", "field", "db", "fnguide", "dart", "dart_restated", "kind"])
    df.to_csv(SRC / "unclassified_fields_20261006.csv", index=False)
    print(len(rows), dict(st))
    print(df.groupby(["field", "kind"]).size().unstack(fill_value=0).to_string())
    fix = df[df.kind == "db_error"]
    if not a.apply or fix.empty:
        return
    run_id = f"unclassified_db_error_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    n = 0
    for tbl, fields, log in (("financial_data", ("revenue", "operating_profit", "net_income", "total_assets", "total_liabilities", "total_equity"), "financial_fix_log"),
                             ("cash_flow_data", ("operating_cf", "investing_cf", "financing_cf", "capex"), "cashflow_fix_log")):
        conn.execute(f"CREATE TABLE IF NOT EXISTS {tbl}_backup_unit_20261005 AS SELECT *, CAST(NULL AS TEXT) run_id FROM {tbl} WHERE false")
        conn.execute(f"SELECT setval('{log}_id_seq',(SELECT MAX(id) FROM {log}))")
        for r in fix[fix.field.isin(fields)].itertuples():
            ann = r.quarter == 0
            row = conn.execute(f"SELECT id, {r.field} FROM {tbl} WHERE stock_code=? AND year=? AND report_type=? AND "
                               + ("is_annual" if ann else "NOT is_annual AND quarter=?") + " ORDER BY id DESC LIMIT 1",
                               (r.code, int(r.year), r.fs) if ann else (r.code, int(r.year), r.fs, int(r.quarter))).fetchone()
            if not row or row[1] is None or abs(row[1] - r.db) > 1:
                continue
            conn.execute(f"INSERT INTO {tbl}_backup_unit_20261005 SELECT *, ? FROM {tbl} WHERE id=?", (run_id, row[0]))
            conn.execute(f"UPDATE {tbl} SET {r.field}=?, updated_at=? WHERE id=?", (float(r.dart), now, row[0]))
            conn.execute(f"""INSERT INTO {log}(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (now, row[0], r.code, int(r.year), int(r.quarter), int(ann), r.fs, r.field, r.db, float(r.dart),
                          "DART 원문 = FnGuide ≠ DB → DART 원문 값(원칙 0 충족)", "DART fnltt + FnGuide wcomp", run_id))
            n += 1
    conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
    conn.execute("INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)",
                 (now, "financial_data+cash_flow_data", "미분류 중 DB 오류 정정", n, "DART 원문 = FnGuide ≠ DB", json.dumps(dict(st)), f"{n}칸",
                  "scripts/review/classify_unclassified_fields_20261006.py", run_id))
    conn.commit()
    print("적용 완료", run_id, n)


if __name__ == "__main__":
    main()
