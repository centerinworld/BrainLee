#!/usr/bin/env python3
"""Correct DART-sourced CFS annual rows (2023+, not data_lock-ed) whose revenue / operating_profit disagree with the live OpenDART
consolidated statement chosen by unambiguous account_id (ifrs-full_Revenue, dart_OperatingIncomeLoss). Scope is deliberately narrow:
  * only rows whose data_source is DART-family (dart, dart_dartfix, dart_redownload, dart_live_recheck_*, dart_rev_fg_fix) -
    FnGuide-sourced rows are rounded to 100M won and define net income as parent-attributable, so they are not compared;
  * net_income is NOT touched (parent-attributable vs total definition ambiguity).
Input /tmp/cfs_fix_list.json = [id,code,year,field,db,live,src]. Old-value guard + financial_fix_log. Dry-run default."""
import json, sys
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
SRC = ("dart", "dart_dartfix", "dart_redownload", "dart_rev_fg_fix")

def main(apply):
    conn = connect_primary_db(timeout=600, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds"); run_id = f"cfs_annual_dart_row_fix_20260924_{datetime.now().strftime('%H%M%S')}"
    locked = {(r[0], r[1]) for r in conn.execute("SELECT stock_code,year FROM data_lock WHERE table_name='financial_data' AND is_locked=1").fetchall()}
    res = {"fixed": 0, "locked": 0, "changed_since": 0, "out_of_scope": 0}; by = {}
    for id_, code, y, f, D, v, src in json.load(open("/tmp/cfs_fix_list.json")):
        if f == "net_income" or not (src in SRC or src.startswith("dart_live_recheck")):
            res["out_of_scope"] += 1; continue
        if (code, y) in locked: res["locked"] += 1; continue
        cur = conn.execute(f"SELECT {f} FROM financial_data WHERE id=?", (id_,)).fetchone()
        if cur is None or (cur[0] is None) != (D is None) or (D is not None and abs(float(cur[0]) - D) > 1e-6):
            res["changed_since"] += 1; continue
        res["fixed"] += 1; by[f] = by.get(f, 0) + 1
        if apply:
            conn.execute(f"UPDATE financial_data SET {f}=? WHERE id=?", (v, id_))
            conn.execute("""INSERT INTO financial_fix_log (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                VALUES (?,?,?,?,0,1,'CFS',?,?,?,?,?,?)""", (now, id_, code, y, f, D, v,
                "DART-source CFS annual row disagreed with live OpenDART consolidated statement value selected by unambiguous account_id",
                f"OpenDART fnlttSinglAcntAll CFS bsns_year={y}; prev_source={src}", run_id))
    if apply: conn.commit()
    conn.close(); print(json.dumps({**res, "by_field": by, "run_id": run_id, "dry_run": not apply}))

if __name__ == "__main__":
    main("--apply" in sys.argv)
