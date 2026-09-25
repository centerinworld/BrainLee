#!/usr/bin/env python3
"""Correct annual financial_data 'dart'/'dart_redownload' CFS rows whose value disagrees with BOTH live OpenDART
(account_id based extract_pl/BS) and FnGuide (which agree with each other, 100M-won rounding tolerated).
Input: /tmp/annual_fixable.json (row id, field, db, live, fg). Rows of data_lock-ed stock-years (financial_data,
is_locked=1) are NOT touched. Guard: the row must still hold the recorded old value. Dry-run default."""
import json, sys
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
COL = {"revenue": "revenue", "op": "operating_profit", "ni": "net_income", "assets": "total_assets", "equity": "total_equity"}

def main(apply):
    conn = connect_primary_db(timeout=300, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds"); run_id = f"annual_dart_row_live_fix_20260924_{datetime.now().strftime('%H%M%S')}"
    res = {"fixed": 0, "locked_skipped": 0, "changed_since": 0}; by = {}
    for code, y, id_, fn, D, v, F in json.load(open("/tmp/annual_fixable.json")):
        f = COL[fn]
        if conn.execute("SELECT 1 FROM data_lock WHERE stock_code=? AND year=? AND table_name='financial_data' AND is_locked=1", (code, y)).fetchone():
            res["locked_skipped"] += 1; continue
        cur = conn.execute(f"SELECT {f} FROM financial_data WHERE id=?", (id_,)).fetchone()
        if cur is None or (cur[0] is None) != (D is None) or (D is not None and abs(float(cur[0]) - D) > 1e-6):
            res["changed_since"] += 1; continue
        res["fixed"] += 1; by[fn] = by.get(fn, 0) + 1
        if apply:
            conn.execute(f"UPDATE financial_data SET {f}=? WHERE id=?", (v, id_))
            conn.execute("""INSERT INTO financial_fix_log (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                VALUES (?,?,?,?,0,1,'CFS',?,?,?,?,?,?)""", (now, id_, code, y, f, D, v,
                "dart-source annual row disagreed with live OpenDART (account_id based) that also matches FnGuide (100M-won rounding); FnGuide+DART two-source agreement",
                f"OpenDART fnlttSinglAcntAll CFS bsns_year={y}; fnguide={F}", run_id))
    if apply:
        conn.commit()
    conn.close(); print(json.dumps({**res, "by_field": by, "run_id": run_id, "dry_run": not apply}))

if __name__ == "__main__":
    main("--apply" in sys.argv)
