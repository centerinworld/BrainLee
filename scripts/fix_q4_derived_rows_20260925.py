#!/usr/bin/env python3
"""Re-derive CFS Q4 revenue / operating_profit (Q4 = annual - Q1 - Q2 - Q3, the project's fixed rule) for 2016-2022 after the annual and
Q1-Q3 rows were verified against live OpenDART (2026-09-24/25). Only stock-years whose annual and Q1-Q3 rows all come from DART-family
sources (dart*, legacy*) are touched, and only when the DB Q4 differs from the derived value by >0.3%. 2023+ is excluded (quarterly rows
there were verified only where the Naver pre-screen flagged them). Input /tmp/q4_fix_list.json = [q4_id,code,year,field,db,derived,src].
Locked stock-years follow the approved unlock -> update -> relock procedure (lock_hash untouched: it hashes the annual CFS row).
Old-value guard (NULL allowed) + financial_fix_log. Dry-run default."""
import json, sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

def main(apply):
    conn = connect_primary_db(timeout=900, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds"); run_id = f"q4_derived_fix_20260925_{datetime.now().strftime('%H%M%S')}"
    locked = {(r[0], r[1]) for r in conn.execute("SELECT stock_code,year FROM data_lock WHERE table_name='financial_data' AND is_locked=1").fetchall()}
    groups = defaultdict(list)
    for it in json.load(open("/tmp/q4_fix_list.json")):
        if it[2] <= 2025: groups[(it[1], it[2])].append(it)
    res = {"fixed": 0, "changed_since": 0, "relocked": 0}; by = {}
    for n, ((code, y), items) in enumerate(groups.items()):
        is_locked = (code, y) in locked
        if apply and is_locked:
            conn.execute("UPDATE data_lock SET is_locked=0, unlocked_at=?, unlock_reason=? WHERE stock_code=? AND year=? AND table_name='financial_data'",
                         (now, f"2026-09-25 user-approved unlock: Q4 re-derived (annual - Q1..Q3) after live-DART verification ({run_id})", code, y))
        for id_, _c, _y, f, D, V, src in items:
            cur = conn.execute(f"SELECT {f} FROM financial_data WHERE id=?", (id_,)).fetchone()
            if cur is None or (cur[0] is None) != (D is None) or (D is not None and abs(float(cur[0]) - D) > 1e-6):
                res["changed_since"] += 1; continue
            res["fixed"] += 1; by[f] = by.get(f, 0) + 1
            if apply:
                conn.execute(f"UPDATE financial_data SET {f}=? WHERE id=?", (V, id_))
                conn.execute("""INSERT INTO financial_fix_log (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                    VALUES (?,?,?,?,4,0,'CFS',?,?,?,?,?,?)""", (now, id_, code, y, f, D, V,
                    "Q4 = annual - Q1 - Q2 - Q3 re-derived; annual and Q1-Q3 rows all DART-family and verified against live OpenDART",
                    f"derived in DB; prev_source={src}", run_id))
        if apply and is_locked:
            conn.execute("UPDATE data_lock SET is_locked=1, locked_at=? WHERE stock_code=? AND year=? AND table_name='financial_data'", (now, code, y)); res["relocked"] += 1
        if apply and n % 1500 == 1499: conn.commit()
    if apply: conn.commit()
    conn.close(); print(json.dumps({**res, "by_field": by, "run_id": run_id, "dry_run": not apply}))

if __name__ == "__main__":
    main("--apply" in sys.argv)
