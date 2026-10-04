#!/usr/bin/env python3
"""재고자산 정본 교체: 본문 파싱값(dart_cost_quarterly, FnGuide 대비 53% 일치·배율 제각각) → DART 재무상태표 `Inventories`(2026-10-04).

원천: fetch_dart_cashflow_20261002.py 가 추출하는 vals['inventory'](재무상태표, ifrs-full_Inventories → 계정명 '재고자산').
원칙 0: **FnGuide 원문(재고자산, 같은 구분·회계기간)과 일치하는 칸만** 반영. 외부 없음·불일치는 후보 파일로만.
기간: DART 1·2·3분기 보고서 → fiscal_quarter 1·2·3, 사업보고서(연간 말 잔액) → fiscal_quarter 4.
대상: dart_cost_quarterly 의 기존 행(inventory_assets_krw)만 갱신(parser_version='dart_bs_inventories', confidence=1.0). 행이 없으면 후보로만.
기본 dry-run, --apply 시 백업(dart_cost_quarterly_backup_inventory_20261004)·data_fix_log.
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


def close(a, b):
    return a is not None and b is not None and abs(a - b) <= max(2e6, abs(b) * 0.005)


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
                v = (d.get("vals") or {}).get("inventory")
                if v is not None and d.get("fs"):
                    dart[(d["code"], d["year"], 4 if d["q"] == 0 else d["q"])] = (v, d["fs"])
    fg = {}
    for p in (RAW.iterdir() if RAW.exists() else []):
        if p.is_dir():
            r, _ = parse_code(p.name)
            for (y, q, fs), v in r.items():
                if isinstance(q, int) and v.get("inventory") is not None:
                    fg[(p.name, y, 4 if q == 0 else q, fs)] = v["inventory"]
    conn = connect_primary_db(timeout=600, readonly=not a.apply)
    nondec = {r[0] for r in conn.execute("SELECT stock_code FROM stock_collection_config WHERE config_key='fs_quirk:fiscal_month' AND config_value<>'12'").fetchall()}
    dart = {k: v for k, v in dart.items() if k[0] not in nondec}  # 비12월 결산: 기간 키 체계가 달라 제외(2026-10-04)
    db = {(r[0], r[1], r[2]): (r[3], r[4]) for r in map(tuple, conn.execute(
        "SELECT stock_code, fiscal_year, fiscal_quarter, inventory_assets_krw, parser_version FROM dart_cost_quarterly").fetchall())}
    st, plan, hold = collections.Counter(), [], []
    for (code, y, q), (v, fs) in dart.items():
        e = fg.get((code, y, q, fs))
        cur = db.get((code, y, q))
        if e is None:
            st["FnGuide 없음 → 보류"] += 1
            hold.append((code, y, q, fs, v, None, cur[0] if cur else None, "외부 없음"))
            continue
        if not close(v, e):
            st["DART≠FnGuide → 보류(파싱 의심)"] += 1
            hold.append((code, y, q, fs, v, e, cur[0] if cur else None, "DART≠FnGuide"))
            continue
        if cur is None:
            st["확인됐지만 대상 행 없음"] += 1
            hold.append((code, y, q, fs, v, e, None, "행 없음"))
            continue
        if close(cur[0], v):
            st["이미 일치"] += 1
            continue
        st["반영(기존값 교체)" if cur[0] is not None else "반영(빈 칸 채움)"] += 1
        plan.append((code, y, q, cur[0], v))
    print(json.dumps(dict(st), ensure_ascii=False, indent=1))
    pd.DataFrame(hold, columns=["stock_code", "fiscal_year", "fiscal_quarter", "fs", "dart", "fnguide", "db", "reason"]).to_csv(
        SRC / "inventory_dart_bs_hold.csv", index=False)
    if not a.apply or not plan:
        return
    run_id = f"inventory_dart_bs_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute("CREATE TABLE IF NOT EXISTS dart_cost_quarterly_backup_inventory_20261004 AS SELECT *, CAST(NULL AS TEXT) run_id FROM dart_cost_quarterly WHERE false")
    for code, y, q, old, new in plan:
        conn.execute("INSERT INTO dart_cost_quarterly_backup_inventory_20261004 SELECT *, ? FROM dart_cost_quarterly WHERE stock_code=? AND fiscal_year=? AND fiscal_quarter=?",
                     (run_id, code, y, q))
        conn.execute("UPDATE dart_cost_quarterly SET inventory_assets_krw=?, parser_version='dart_bs_inventories', confidence=1.0, updated_at=? "
                     "WHERE stock_code=? AND fiscal_year=? AND fiscal_quarter=?", (new, now, code, y, q))
    conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")
    conn.execute("INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id) VALUES (?,?,?,?,?,?,?,?,?)",
                 (now, "dart_cost_quarterly", "재고자산 정본 교체(FnGuide 확인분)", len(plan), "본문 파싱 → DART 재무상태표 Inventories, FnGuide 일치 칸만",
                  json.dumps(dict(st), ensure_ascii=False), "DART BS + FnGuide", "scripts/review/apply_inventory_from_dart_bs_20261004.py", run_id))
    conn.commit()
    print("적용 완료", run_id, len(plan))


if __name__ == "__main__":
    main()
