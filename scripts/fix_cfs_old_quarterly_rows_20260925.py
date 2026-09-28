#!/usr/bin/env python3
"""Correct CFS quarterly (Q1-Q3, 2016-2022) revenue / operating_profit / total_assets rows that disagree with the live OpenDART
consolidated statement (unambiguous account_id; thstrm_amount = 3-month P&L value, quarter-end balance sheet). DART/legacy-sourced
rows use a tight tolerance (0.3%); FnGuide/other-sourced rounded rows only when off by >5% and >=1e9 won. Input /tmp/cfs_q_old_fix.json
= [id,code,year,quarter,field,db,live,src]. data_lock-ed stock-years (user-approved procedure): is_locked=0 + unlock_reason ->
UPDATEs -> is_locked=1 (lock_hash untouched: it hashes the annual CFS row only). Old-value guard + financial_fix_log. Dry-run default."""
import json, sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

def main(apply):
    conn = connect_primary_db(timeout=900, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds"); run_id = f"cfs_old_quarterly_fix_20260925_{datetime.now().strftime('%H%M%S')}"
    locked = {(r[0], r[1]) for r in conn.execute("SELECT stock_code,year FROM data_lock WHERE table_name='financial_data' AND is_locked=1").fetchall()}
    groups = defaultdict(list)
    for it in json.load(open("/tmp/cfs_q_old_fix.json")): groups[(it[1], it[2])].append(it)
    res = {"fixed": 0, "changed_since": 0, "relocked": 0}; by = {}
    for n, ((code, y), items) in enumerate(groups.items()):
        is_locked = (code, y) in locked
        if apply and is_locked:
            conn.execute("UPDATE data_lock SET is_locked=0, unlocked_at=?, unlock_reason=? WHERE stock_code=? AND year=? AND table_name='financial_data'",
                         (now, f"2026-09-25 user-approved unlock: CFS quarterly rows disagreed with live OpenDART consolidated statement ({run_id})", code, y))
        for id_, _c, _y, q, f, D, L, src in items:
            cur = conn.execute(f"SELECT {f} FROM financial_data WHERE id=?", (id_,)).fetchone()
            if cur is None or cur[0] is None or abs(float(cur[0]) - D) > 1e-6:
                res["changed_since"] += 1; continue
            res["fixed"] += 1; by[f] = by.get(f, 0) + 1
            if apply:
                conn.execute(f"UPDATE financial_data SET {f}=? WHERE id=?", (L, id_))
                conn.execute("""INSERT INTO financial_fix_log (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                    VALUES (?,?,?,?,?,0,'CFS',?,?,?,?,?,?)""", (now, id_, code, y, q, f, D, L,
                    "CFS quarterly row disagreed with live OpenDART consolidated statement value selected by unambiguous account_id",
                    f"OpenDART fnlttSinglAcntAll CFS bsns_year={y} quarter={q}; prev_source={src}", run_id))
        if apply and is_locked:
            conn.execute("UPDATE data_lock SET is_locked=1, locked_at=? WHERE stock_code=? AND year=? AND table_name='financial_data'", (now, code, y)); res["relocked"] += 1
        if apply and n % 1500 == 1499: conn.commit()
    if apply: conn.commit()
    conn.close(); print(json.dumps({**res, "by_field": by, "run_id": run_id, "dry_run": not apply}))

if __name__ == "__main__":
    main("--apply" in sys.argv)
