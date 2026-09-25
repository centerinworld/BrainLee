#!/usr/bin/env python3
"""Correct OFS annual financial_data rows for 2016-2022 whose revenue/operating_profit/net_income disagree with the live OpenDART
separate statement chosen by unambiguous account_id. Input /tmp/ofs_old_fix_list.json = [id,code,year,field,db,live,src].
data_lock-ed stock-years (user-approved unlock procedure, 2026-09-24): is_locked=0 + unlocked_at/unlock_reason -> UPDATEs ->
is_locked=1 + locked_at. lock_hash is left unchanged: it is md5(revenue|operating_profit|net_income) of the annual CFS 'dart' row,
which this script does not modify. Old-value guard + financial_fix_log. Dry-run default."""
import json, sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

def main(apply):
    conn = connect_primary_db(timeout=600, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds"); run_id = f"ofs_old_annual_fix_20260924_{datetime.now().strftime('%H%M%S')}"
    locked = {(r[0], r[1]) for r in conn.execute("SELECT stock_code,year FROM data_lock WHERE table_name='financial_data' AND is_locked=1").fetchall()}
    groups = defaultdict(list)
    for it in json.load(open("/tmp/ofs_old_fix_list.json")):
        groups[(it[1], it[2])].append(it)
    res = {"fixed": 0, "changed_since": 0, "relocked_stock_years": 0}; by = {}
    for (code, y), items in groups.items():
        is_locked = (code, y) in locked
        if apply and is_locked:
            conn.execute("UPDATE data_lock SET is_locked=0, unlocked_at=?, unlock_reason=? WHERE stock_code=? AND year=? AND table_name='financial_data'",
                         (now, f"2026-09-24 user-approved unlock: OFS annual rows disagreed with live OpenDART separate statement ({run_id})", code, y))
        for id_, _c, _y, f, D, v, src in items:
            cur = conn.execute(f"SELECT {f} FROM financial_data WHERE id=?", (id_,)).fetchone()
            if cur is None or (cur[0] is None) != (D is None) or (D is not None and abs(float(cur[0]) - D) > 1e-6):
                res["changed_since"] += 1; continue
            res["fixed"] += 1; by[f] = by.get(f, 0) + 1
            if apply:
                conn.execute(f"UPDATE financial_data SET {f}=? WHERE id=?", (v, id_))
                conn.execute("""INSERT INTO financial_fix_log (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                    VALUES (?,?,?,?,0,1,'OFS',?,?,?,?,?,?)""", (now, id_, code, y, f, D, v,
                    "OFS annual row disagreed with live OpenDART separate statement value selected by unambiguous account_id (legacy dart_ofs_backfill misparse)",
                    f"OpenDART fnlttSinglAcntAll OFS bsns_year={y}; prev_source={src}", run_id))
        if apply and is_locked:
            conn.execute("UPDATE data_lock SET is_locked=1, locked_at=? WHERE stock_code=? AND year=? AND table_name='financial_data'", (now, code, y))
            res["relocked_stock_years"] += 1
    if apply: conn.commit()
    conn.close(); print(json.dumps({**res, "by_field": by, "run_id": run_id, "dry_run": not apply}))

if __name__ == "__main__":
    main("--apply" in sys.argv)
