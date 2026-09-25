#!/usr/bin/env python3
"""Correct OFS annual financial_data rows (2023+, not data_lock-ed) whose revenue/operating_profit/net_income disagree with
the live OpenDART separate statement selected by unambiguous account_id (ifrs-full_Revenue, dart_OperatingIncomeLoss,
ifrs-full_ProfitLoss). Input /tmp/ofs_fix_list.json = [id,code,year,field,db,live,src]. Guard: row must still hold the recorded
old value. Old values kept in financial_fix_log. Dry-run default."""
import json, sys
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

def main(apply):
    conn = connect_primary_db(timeout=600, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds"); run_id = f"ofs_annual_live_fix_20260924_{datetime.now().strftime('%H%M%S')}"
    locked = {(r[0], r[1]) for r in conn.execute("SELECT stock_code,year FROM data_lock WHERE table_name='financial_data' AND is_locked=1").fetchall()}
    res = {"fixed": 0, "locked": 0, "changed_since": 0}; by = {}
    for n, (id_, code, y, f, D, v, src) in enumerate(json.load(open("/tmp/ofs_fix_list.json"))):
        if (code, y) in locked: res["locked"] += 1; continue
        cur = conn.execute(f"SELECT {f} FROM financial_data WHERE id=?", (id_,)).fetchone()
        if cur is None or (cur[0] is None) != (D is None) or (D is not None and abs(float(cur[0]) - D) > 1e-6):
            res["changed_since"] += 1; continue
        res["fixed"] += 1; by[f] = by.get(f, 0) + 1
        if apply:
            conn.execute(f"UPDATE financial_data SET {f}=? WHERE id=?", (v, id_))
            conn.execute("""INSERT INTO financial_fix_log (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                VALUES (?,?,?,?,0,1,'OFS',?,?,?,?,?,?)""", (now, id_, code, y, f, D, v,
                "OFS annual row disagreed with live OpenDART separate statement value selected by unambiguous account_id (misparsed by legacy dart_ofs_backfill / rev_fg_fix)",
                f"OpenDART fnlttSinglAcntAll OFS bsns_year={y}; prev_source={src}", run_id))
        if apply and n % 2000 == 1999: conn.commit()
    if apply: conn.commit()
    conn.close(); print(json.dumps({**res, "by_field": by, "run_id": run_id, "dry_run": not apply}))

if __name__ == "__main__":
    main("--apply" in sys.argv)
