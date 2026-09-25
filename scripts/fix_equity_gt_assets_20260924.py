#!/usr/bin/env python3
"""Fix financial_data rows with impossible total_equity > total_assets using the live OpenDART balance sheet
(/tmp/eq_gt_assets.json). Only when the live statement is coherent AND exactly one of the two DB fields already
matches it: (a) DB equity matches live (<=0.5%) but assets differ -> assets := live;
(b) DB assets match live (<=0.1%) but equity differs -> equity := live. Anything else is reported only.
id-targeted UPDATE with old-value guard + financial_fix_log. Dry-run default."""
import json, sys
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

def close(a, b, tol): return abs(a - b) <= max(1.0, abs(b) * tol)

def main(apply):
    conn = connect_primary_db(timeout=300, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds"); run_id = f"equity_gt_assets_fix_20260924_{datetime.now().strftime('%H%M%S')}"
    res = {"fixed_assets": 0, "fixed_equity": 0, "skipped": 0, "locked": 0}; skipped = []
    for r in json.load(open("/tmp/eq_gt_assets.json")):
        la, le, da, de = r["live_a"], r["live_e"], r["db_a"], r["db_e"]
        if la is None or le is None or la < le:
            res["skipped"] += 1; skipped.append((r["code"], r["year"], r["quarter"], "no coherent live")); continue
        if conn.execute("SELECT 1 FROM data_lock WHERE stock_code=? AND year=? AND table_name='financial_data' AND is_locked=1", (r["code"], r["year"])).fetchone():
            res["locked"] += 1; continue
        if close(le, de, 0.005) and not close(la, da, 0.001):
            field, old, new = "total_assets", da, la
        elif close(la, da, 0.001) and not close(le, de, 0.001):
            field, old, new = "total_equity", de, le
        else:
            res["skipped"] += 1; skipped.append((r["code"], r["year"], r["quarter"], "ambiguous")); continue
        cur = conn.execute(f"SELECT {field} FROM financial_data WHERE id=?", (r["id"],)).fetchone()
        if not cur or cur[0] is None or abs(float(cur[0]) - old) > 1e-6:
            res["skipped"] += 1; continue
        res["fixed_assets" if field == "total_assets" else "fixed_equity"] += 1
        if apply:
            conn.execute(f"UPDATE financial_data SET {field}=? WHERE id=?", (new, r["id"]))
            conn.execute("""INSERT INTO financial_fix_log (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (now, r["id"], r["code"], r["year"], 0 if r["ann"] else r["quarter"], 1 if r["ann"] else 0, r["rt"], field, old, new,
                "impossible equity>assets; live OpenDART balance sheet coherent and matches the other DB field, so the mismatching field is corrected",
                f"OpenDART fnlttSinglAcntAll {r['rt']} bsns_year={r['year']} quarter={r['quarter']}", run_id))
    if apply:
        conn.commit()
    conn.close(); print(json.dumps({**res, "run_id": run_id, "dry_run": not apply})); print(skipped)

if __name__ == "__main__":
    main("--apply" in sys.argv)
