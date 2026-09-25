#!/usr/bin/env python3
"""Correct DART-sourced CFS annual rows for 2016-2022 whose revenue / operating_profit disagree with the live OpenDART consolidated
statement chosen by unambiguous account_id (input /tmp/cfs_old_fix_list.json; FnGuide-sourced rows and net income excluded, see
fix_cfs_annual_dart_rows_20260924.py). data_lock-ed stock-years follow the user-approved procedure: is_locked=0 + unlock reason ->
UPDATEs -> is_locked=1, locked_at, lock_hash = md5('revenue|operating_profit|net_income') of the corrected annual CFS dart row.
Old-value guard + financial_fix_log. Dry-run default."""
import hashlib, json, sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

def main(apply):
    conn = connect_primary_db(timeout=600, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds"); run_id = f"cfs_old_annual_fix_20260924_{datetime.now().strftime('%H%M%S')}"
    locked = {(r[0], r[1]) for r in conn.execute("SELECT stock_code,year FROM data_lock WHERE table_name='financial_data' AND is_locked=1").fetchall()}
    groups = defaultdict(list)
    for it in json.load(open("/tmp/cfs_old_fix_list.json")): groups[(it[1], it[2])].append(it)
    res = {"fixed": 0, "changed_since": 0, "relocked": 0}; by = {}
    for (code, y), items in groups.items():
        is_locked = (code, y) in locked; touched = set()
        if apply and is_locked:
            conn.execute("UPDATE data_lock SET is_locked=0, unlocked_at=?, unlock_reason=? WHERE stock_code=? AND year=? AND table_name='financial_data'",
                         (now, f"2026-09-24 user-approved unlock: DART CFS annual row disagreed with live OpenDART consolidated statement ({run_id})", code, y))
        for id_, _c, _y, f, D, v, src in items:
            cur = conn.execute(f"SELECT {f} FROM financial_data WHERE id=?", (id_,)).fetchone()
            if cur is None or (cur[0] is None) != (D is None) or (D is not None and abs(float(cur[0]) - D) > 1e-6):
                res["changed_since"] += 1; continue
            res["fixed"] += 1; by[f] = by.get(f, 0) + 1; touched.add(id_)
            if apply:
                conn.execute(f"UPDATE financial_data SET {f}=? WHERE id=?", (v, id_))
                conn.execute("""INSERT INTO financial_fix_log (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                    VALUES (?,?,?,?,0,1,'CFS',?,?,?,?,?,?)""", (now, id_, code, y, f, D, v,
                    "DART-source CFS annual row disagreed with live OpenDART consolidated statement value selected by unambiguous account_id",
                    f"OpenDART fnlttSinglAcntAll CFS bsns_year={y}; prev_source={src}", run_id))
        if apply and is_locked:
            new_hash = None
            for id_ in touched:
                r = conn.execute("SELECT revenue,operating_profit,net_income FROM financial_data WHERE id=?", (id_,)).fetchone()
                new_hash = hashlib.md5(f"{r[0]}|{r[1]}|{r[2]}".encode()).hexdigest()
            conn.execute("UPDATE data_lock SET is_locked=1, locked_at=?, lock_hash=COALESCE(?, lock_hash) WHERE stock_code=? AND year=? AND table_name='financial_data'", (now, new_hash, code, y))
            res["relocked"] += 1
    if apply: conn.commit()
    conn.close(); print(json.dumps({**res, "by_field": by, "run_id": run_id, "dry_run": not apply}))

if __name__ == "__main__":
    main("--apply" in sys.argv)
