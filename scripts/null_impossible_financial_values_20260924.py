#!/usr/bin/env python3
"""NULL out impossible financial_data values (same policy as the 2026-09 negative-revenue cleanup and the
'no forced Q4 derivation, keep NULL + review' rule): revenue < 0 (any row) and total_assets <= 0 (plus
total_equity when it is exactly 0 on such a row - a placeholder). data_lock-ed stock-years are skipped.
id-targeted UPDATE + financial_fix_log (old value kept). Dry-run default."""
import json, sys
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

def main(apply):
    conn = connect_primary_db(timeout=300, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds"); run_id = f"null_impossible_values_20260924_{datetime.now().strftime('%H%M%S')}"
    res = {"revenue_nulled": 0, "assets_nulled": 0, "equity_nulled": 0, "locked_skipped": 0}
    def locked(code, y):
        return conn.execute("SELECT 1 FROM data_lock WHERE stock_code=? AND year=? AND table_name='financial_data' AND is_locked=1", (code, y)).fetchone()
    def do(id_, code, y, q, ann, rt, field, old, why):
        if apply:
            conn.execute(f"UPDATE financial_data SET {field}=NULL WHERE id=? AND {field}=?", (id_, old))
            conn.execute("""INSERT INTO financial_fix_log (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                VALUES (?,?,?,?,?,?,?,?,?,NULL,?,?,?)""", (now, id_, code, y, q, 1 if ann else 0, rt, field, old, why, "integrity scan 2026-09-24", run_id))
    for id_, code, y, q, ann, rt, rev in [tuple(r) for r in conn.execute("SELECT id,stock_code,year,quarter,is_annual,report_type,revenue FROM financial_data WHERE revenue<0").fetchall()]:
        if locked(code, y): res["locked_skipped"] += 1; continue
        res["revenue_nulled"] += 1; do(id_, code, y, q, ann, rt, "revenue", rev, "NEGATIVE_REVENUE_NULLED: impossible value (derived/parsed), kept NULL for review")
    for id_, code, y, q, ann, rt, a, e in [tuple(r) for r in conn.execute("SELECT id,stock_code,year,quarter,is_annual,report_type,total_assets,total_equity FROM financial_data WHERE total_assets<=0").fetchall()]:
        if locked(code, y): res["locked_skipped"] += 1; continue
        res["assets_nulled"] += 1; do(id_, code, y, q, ann, rt, "total_assets", a, "ZERO_ASSETS_NULLED: non-positive total assets is a placeholder, kept NULL for review")
        if e is not None and float(e) == 0.0:
            res["equity_nulled"] += 1; do(id_, code, y, q, ann, rt, "total_equity", e, "ZERO_EQUITY_NULLED: placeholder alongside non-positive assets")
    if apply:
        conn.commit()
    conn.close(); print(json.dumps({**res, "run_id": run_id, "dry_run": not apply}))

if __name__ == "__main__":
    main("--apply" in sys.argv)
