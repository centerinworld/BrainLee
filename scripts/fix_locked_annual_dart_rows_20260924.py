#!/usr/bin/env python3
"""Correct data_lock-ed (financial_data, 2021/2022, lock_basis dart_verified) annual 'dart' rows whose value
disagrees with BOTH live OpenDART and FnGuide (user-approved unlock, 2026-09-24). Per stock-year:
  1. data_lock: is_locked=0, unlock_reason, unlocked_at  (audit trail)
  2. id-targeted UPDATE of each field (guard: row still holds recorded old value) + financial_fix_log
  3. re-lock: is_locked=1, locked_at=now, lock_hash=md5('revenue|operating_profit|net_income') of the corrected
     row (same formula as the original lock hashes), unlock_reason/unlocked_at retained.
Dry-run default; --apply writes."""
import hashlib, json, sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
COL = {"revenue": "revenue", "op": "operating_profit", "ni": "net_income", "assets": "total_assets", "equity": "total_equity"}

def main(apply):
    conn = connect_primary_db(timeout=300, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds"); run_id = f"locked_annual_dart_fix_20260924_{datetime.now().strftime('%H%M%S')}"
    groups = defaultdict(list)
    for code, y, id_, fn, D, v, F in json.load(open("/tmp/annual_fixable.json")):
        if conn.execute("SELECT 1 FROM data_lock WHERE stock_code=? AND year=? AND table_name='financial_data' AND is_locked=1", (code, y)).fetchone():
            groups[(code, y)].append((id_, fn, D, v, F))
    res = {"stock_years": len(groups), "fields": 0, "changed_since": 0}
    for (code, y), items in groups.items():
        if apply:
            conn.execute("""UPDATE data_lock SET is_locked=0, unlocked_at=?, unlock_reason=? WHERE stock_code=? AND year=? AND table_name='financial_data'""",
                         (now, f"2026-09-24 user-approved unlock: annual dart row disagreed with live OpenDART+FnGuide ({run_id})", code, y))
        touched = set()
        for id_, fn, D, v, F in items:
            f = COL[fn]
            cur = conn.execute(f"SELECT {f} FROM financial_data WHERE id=?", (id_,)).fetchone()
            if cur is None or (cur[0] is None) != (D is None) or (D is not None and abs(float(cur[0]) - D) > 1e-6):
                res["changed_since"] += 1; continue
            res["fields"] += 1; touched.add(id_)
            if apply:
                conn.execute(f"UPDATE financial_data SET {f}=? WHERE id=?", (v, id_))
                conn.execute("""INSERT INTO financial_fix_log (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                    VALUES (?,?,?,?,0,1,'CFS',?,?,?,?,?,?)""", (now, id_, code, y, f, D, v,
                    "locked dart_verified row disagreed with live OpenDART (account_id based) and FnGuide which agree; unlocked by user approval, corrected, re-locked",
                    f"OpenDART fnlttSinglAcntAll CFS bsns_year={y}; fnguide={F}", run_id))
        if apply:
            new_hash = None
            for id_ in touched:
                r = conn.execute("SELECT revenue,operating_profit,net_income FROM financial_data WHERE id=?", (id_,)).fetchone()
                new_hash = hashlib.md5(f"{r[0]}|{r[1]}|{r[2]}".encode()).hexdigest()
            conn.execute("""UPDATE data_lock SET is_locked=1, locked_at=?, lock_hash=COALESCE(?, lock_hash) WHERE stock_code=? AND year=? AND table_name='financial_data'""",
                         (now, new_hash, code, y))
    if apply:
        conn.commit()
    conn.close(); print(json.dumps({**res, "run_id": run_id, "dry_run": not apply}))

if __name__ == "__main__":
    main("--apply" in sys.argv)
