#!/usr/bin/env python3
"""Correct OFS quarterly (Q1-Q3, 2023+) operating_profit rows from the live OpenDART separate statement (unambiguous account_id
dart_OperatingIncomeLoss / ifrs-full_OperatingIncomeLoss; thstrm_amount = 3-month value). Sample check 2026-09-25: OFS quarterly revenue
(144/144) and total_assets (149/149) matched DART, operating_profit did not (97/145) - legacy dart_ofs_backfill misparse. Input:
/tmp/ofs_q_op_fix.json = [id,code,year,quarter,db,live,src]. Old-value guard + financial_fix_log. Dry-run default."""
import json, sys
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

def main(apply):
    conn = connect_primary_db(timeout=600, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds"); run_id = f"ofs_quarterly_op_fix_20260924_{datetime.now().strftime('%H%M%S')}"
    locked = {(r[0], r[1]) for r in conn.execute("SELECT stock_code,year FROM data_lock WHERE table_name='financial_data' AND is_locked=1").fetchall()}
    res = {"fixed": 0, "locked": 0, "changed_since": 0}
    for n, (id_, code, y, q, D, L, src) in enumerate(json.load(open("/tmp/ofs_q_op_fix.json"))):
        if (code, y) in locked: res["locked"] += 1; continue
        cur = conn.execute("SELECT operating_profit FROM financial_data WHERE id=?", (id_,)).fetchone()
        if cur is None or (cur[0] is None) != (D is None) or (D is not None and abs(float(cur[0]) - D) > 1e-6):
            res["changed_since"] += 1; continue
        res["fixed"] += 1
        if apply:
            conn.execute("UPDATE financial_data SET operating_profit=? WHERE id=?", (L, id_))
            conn.execute("""INSERT INTO financial_fix_log (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                VALUES (?,?,?,?,?,0,'OFS','operating_profit',?,?,?,?,?)""", (now, id_, code, y, q, D, L,
                "OFS quarterly operating_profit disagreed with live OpenDART separate statement 3-month value (account_id); legacy dart_ofs_backfill misparse",
                f"OpenDART fnlttSinglAcntAll OFS bsns_year={y} quarter={q}; prev_source={src}", run_id))
            if n % 3000 == 2999: conn.commit()
    if apply: conn.commit()
    conn.close(); print(json.dumps({**res, "run_id": run_id, "dry_run": not apply}))

if __name__ == "__main__":
    main("--apply" in sys.argv)
