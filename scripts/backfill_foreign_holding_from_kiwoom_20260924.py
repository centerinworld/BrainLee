#!/usr/bin/env python3
"""Backfill foreign_holding_daily (stopped after 2026-06-08) from kiwoom_foreign_flow (ka10008): verified on 8,539
overlapping rows (2026-06-01~06-08) that frgn_hold_pct == weight and frgn_hold_qty == poss_stock_cnt for 100%.
Insert-only (existing rows are never touched); frgn_limit_pct left NULL (the overlap rows also had NULL).
created_at carries the run_id marker so the inserted rows can be removed (rollback). Dry-run default."""
import json, sys
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

def main(apply):
    conn = connect_primary_db(timeout=300, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds"); run_id = f"foreign_holding_kiwoom_backfill_20260924_{datetime.now().strftime('%H%M%S')}"
    sel = """SELECT k.dt,k.stock_code,COALESCE(u.stock_name,''),k.poss_stock_cnt,k.weight FROM kiwoom_foreign_flow k
        LEFT JOIN (SELECT DISTINCT ON (stock_code) stock_code,stock_name FROM stock_universe ORDER BY stock_code,base_date DESC) u ON u.stock_code=k.stock_code
        WHERE k.dt>'20260608' AND k.weight IS NOT NULL AND k.poss_stock_cnt IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM foreign_holding_daily f WHERE f.stock_code=k.stock_code AND f.bas_dt=k.dt)"""
    rows = [tuple(r) for r in conn.execute(sel).fetchall()]
    by = {}
    for r in rows: by[r[0]] = by.get(r[0], 0) + 1
    print("rows to insert:", len(rows), "days:", len(by), "first/last:", min(by), max(by), "last days:", sorted(by.items())[-3:])
    if apply and rows:
        conn.executemany("""INSERT INTO foreign_holding_daily (bas_dt,stock_code,stock_name,frgn_hold_qty,frgn_hold_pct,frgn_limit_pct,created_at)
            VALUES (?,?,?,?,?,NULL,?)""", [(d, c, n, float(q), float(w), f"{now} {run_id}") for d, c, n, q, w in rows])
        conn.execute("""INSERT INTO data_fix_log (fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)
            VALUES (?,?,?,?,?,?,?,?,?)""", (now, "foreign_holding_daily", "2026-06-09~2026-09-23 (table stopped after 06-08)", len(rows),
            "INSERT missing rows from kiwoom_foreign_flow (weight->pct, poss_stock_cnt->qty; 100% equal on 8,539 overlap rows)", "no rows", "kiwoom ka10008 foreign holding",
            "kiwoom_foreign_flow", run_id))
        conn.commit()
    conn.close(); print(json.dumps({"rows": len(rows), "run_id": run_id, "dry_run": not apply}))

if __name__ == "__main__":
    main("--apply" in sys.argv)
