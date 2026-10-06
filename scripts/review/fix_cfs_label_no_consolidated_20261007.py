#!/usr/bin/env python3
"""연결재무제표가 없는 회사의 '연결(CFS)' 표기 행 바로잡기(2026-10-07, 결정 A6 — REVIEW_PLAN_20261006.md §13).

배경: 연결재무제표를 만들지 않는 회사인데 FnGuide 경로가 별도 값을 '연결'로 저장(2026Q1 등), DART 경로는 '별도'로 저장 →
      같은 회사의 분기가 연결/별도로 섞여 분기 비교·TTM이 깨짐(2026Q2 연결 결측 453종목 중 425).
규칙(정본 FINANCIAL_STATEMENTS.md §2-8): 연결재무제표가 없는 회사는 '별도'가 대표값.
판정(키 단위, 2023+ — DART 원문 dart_cf_full.jsonl 범위):
  근거 = 그 (연도, 분기)의 DART 원문이 OFS뿐(DART는 연결이 있으면 CFS를 준다).
  · 같은 키의 OFS 행이 있고 CFS 행 값(매출·자산총계)이 1% 이내로 같다 → CFS 행은 중복 → 삭제(백업)
  · OFS 행이 없고 CFS 행 값이 DART OFS 원문과 1% 이내 → report_type 을 OFS 로 변경
  · 그 밖 → 보류(목록)
financial_data·cash_flow_data 둘 다(현금흐름은 영업·투자 현금흐름으로 비교). 기본 dry-run, --apply.
"""
import argparse
import collections
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

SRC = ROOT / "research_outputs" / "financial_rereview_20261002"


def near(a, b):
    return a is not None and b is not None and abs(a - b) <= max(1e8, abs(b) * 0.01)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    fs_of = collections.defaultdict(set)
    dval = {}
    for line in open(SRC / "dart_cf_full.jsonl"):
        d = json.loads(line)
        if d.get("ok") and d.get("fs"):
            fs_of[(d["code"], d["year"], d["q"])].add(d["fs"])
            if d["fs"] == "OFS":
                dval[(d["code"], d["year"], d["q"])] = d.get("vals") or {}
    conn = connect_primary_db(timeout=600, readonly=not a.apply)
    fx = {r[0] for r in conn.execute("SELECT stock_code FROM stock_collection_config WHERE config_key IN ('fs_quirk:reporting_currency','fs_quirk:fiscal_month') AND config_value NOT IN ('KRW','12')").fetchall()}
    plan, hold, st = [], [], collections.Counter()
    for tbl, k1, k2, d1, d2 in (("financial_data", "revenue", "total_assets", "revenue", "total_assets"),
                                ("cash_flow_data", "operating_cf", "investing_cf", "ocf", "icf")):
        rows = [tuple(r) for r in conn.execute(f"SELECT id, stock_code, year, quarter, is_annual, {k1}, {k2} FROM {tbl} WHERE report_type='CFS' AND year>=2023").fetchall()]
        ofs = {(r[1], r[2], r[3], bool(r[4])): (r[0], r[5], r[6]) for r in map(tuple, conn.execute(
            f"SELECT id, stock_code, year, quarter, is_annual, {k1}, {k2} FROM {tbl} WHERE report_type='OFS' AND year>=2023").fetchall())}
        for rid, code, y, q, ann, v1, v2 in rows:
            if code in fx:
                continue
            dq = 0 if (ann or q == 4) else q
            if q == 4 and not ann:
                continue  # 4분기 파생 행은 연간·1~3분기 정리 후 재계산 대상
            if fs_of.get((code, y, dq)) != {"OFS"}:
                continue
            o = ofs.get((code, y, q, bool(ann)))
            if o:
                if (near(v1, o[1]) or (v1 is None and o[1] is None)) and (near(v2, o[2]) or (v2 is None and o[2] is None)):
                    plan.append((tbl, "delete_dup", rid, code, y, q, ann)); st[f"{tbl}: 별도 행과 같은 값 → 중복 삭제"] += 1
                else:
                    hold.append((tbl, rid, code, y, q, ann, "별도 행과 값 다름")); st[f"{tbl}: 보류(별도 행과 값 다름)"] += 1
            else:
                dv = dval.get((code, y, dq), {})
                if near(v1, dv.get(d1)) or (v1 is None and near(v2, dv.get(d2))):
                    plan.append((tbl, "relabel", rid, code, y, q, ann)); st[f"{tbl}: DART 별도 원문과 일치 → 별도로 표기 변경"] += 1
                else:
                    hold.append((tbl, rid, code, y, q, ann, "DART 별도 원문과 불일치")); st[f"{tbl}: 보류(원문과 불일치)"] += 1
    print(json.dumps(dict(st), ensure_ascii=False, indent=1), "종목", len({p[3] for p in plan}))
    json.dump(hold, open(SRC / "cfs_label_hold_20261007.json", "w"), ensure_ascii=False, default=str)
    if not a.apply or not plan:
        return
    run_id = f"cfs_label_fix_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    for tbl in ("financial_data", "cash_flow_data"):
        conn.execute(f"CREATE TABLE IF NOT EXISTS {tbl}_backup_cfs_label_20261007 AS SELECT *, CAST(NULL AS TEXT) run_id FROM {tbl} WHERE false")
    for tbl, act, rid, *_ in plan:
        conn.execute(f"INSERT INTO {tbl}_backup_cfs_label_20261007 SELECT *, ? FROM {tbl} WHERE id=?", (run_id, rid))
        if act == "delete_dup":
            conn.execute(f"DELETE FROM {tbl} WHERE id=?", (rid,))
        else:
            conn.execute(f"UPDATE {tbl} SET report_type='OFS', data_source=COALESCE(data_source,'')||'+cfs_label_fix', updated_at=? WHERE id=?", (now, rid))
    conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
    conn.execute("INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)",
                 (now, "financial_data+cash_flow_data", "연결 미작성 회사의 '연결' 표기 정리(결정 A6)", len(plan),
                  "DART 원문이 별도뿐인 키: 별도와 같은 값 CFS → 삭제, 별도 없음 + 원문 일치 → OFS로 표기", json.dumps(dict(st), ensure_ascii=False), "",
                  "scripts/review/fix_cfs_label_no_consolidated_20261007.py", run_id))
    conn.commit()
    print("적용 완료", run_id, len(plan))


if __name__ == "__main__":
    main()
