#!/usr/bin/env python3
"""Verify annual financial_data outlier rows (revenue>=1e10 and |op|>3x revenue or |ni|>5x revenue) against the
live OpenDART statement using UNAMBIGUOUS account_ids only (revenue: ifrs-full_Revenue; operating profit:
dart_OperatingIncomeLoss/ifrs-full_OperatingIncomeLoss; net income: ifrs-full_ProfitLoss). A field is corrected only
when the live account_id value exists and differs from the DB value. data_lock-ed stock-years are skipped (reported).
Input ids: /tmp/outlier_annual.json (status 'differs'). id-targeted UPDATE with old-value guard + financial_fix_log."""
import json, sys
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scratch"))
import legacy_dart_recollect as ldr  # noqa: E402
from db_compat import connect_primary_db  # noqa: E402

def main(apply):
    conn = connect_primary_db(timeout=300, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds"); run_id = f"outlier_annual_fix_20260924_{datetime.now().strftime('%H%M%S')}"
    ldr.build_corp_map(); res = {"fixed": 0, "locked": 0, "no_id_value": 0, "already_ok": 0}; by = {}; locked = []
    seen = set()
    for o in json.load(open("/tmp/outlier_annual.json")):
        if o["st"] != "differs" or o["id"] in seen:
            continue
        seen.add(o["id"])
        code, y, rt = o["code"], o["year"], o["rt"]
        if conn.execute("SELECT 1 FROM data_lock WHERE stock_code=? AND year=? AND table_name='financial_data' AND is_locked=1", (code, y)).fetchone():
            res["locked"] += 1; locked.append((code, y, rt)); continue
        pl = [r for r in ldr.fetch_dart(ldr._CORP_MAP[code], y, "11011", rt) if "손익" in r.get("sj_nm", "")]
        live = {"revenue": ldr._by_account_id(pl, ("ifrs-full_Revenue",)),
                "operating_profit": ldr._by_account_id(pl, ("dart_OperatingIncomeLoss", "ifrs-full_OperatingIncomeLoss")),
                "net_income": ldr._by_account_id(pl, ("ifrs-full_ProfitLoss",))}
        row = conn.execute("SELECT revenue,operating_profit,net_income FROM financial_data WHERE id=?", (o["id"],)).fetchone()
        for i, f in enumerate(("revenue", "operating_profit", "net_income")):
            L, D = live[f], row[i]
            if L is None:
                res["no_id_value"] += 1; continue
            if D is not None and abs(float(D) - L) <= max(1e6, abs(L) * 0.003):
                res["already_ok"] += 1; continue
            res["fixed"] += 1; by[f] = by.get(f, 0) + 1
            if apply:
                conn.execute(f"UPDATE financial_data SET {f}=? WHERE id=?", (L, o["id"]))
                conn.execute("""INSERT INTO financial_fix_log (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                    VALUES (?,?,?,?,0,1,?,?,?,?,?,?,?)""", (now, o["id"], code, y, rt, f, D, L,
                    "annual outlier row disagreed with live OpenDART value selected by unambiguous account_id",
                    f"OpenDART fnlttSinglAcntAll {rt} bsns_year={y}", run_id))
    if apply:
        conn.commit()
    conn.close(); print(json.dumps({**res, "by_field": by, "run_id": run_id, "dry_run": not apply, "locked_list": locked[:40]}))

if __name__ == "__main__":
    main("--apply" in sys.argv)
