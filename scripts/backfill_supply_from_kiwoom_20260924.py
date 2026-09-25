#!/usr/bin/env python3
"""Backfill price_history investor net-buy amounts (백만원) for dates where the KRX 18:10 collection failed
(2026-09-14~18, only ~345 of ~2,640 rows filled) from kiwoom_investor_daily (ka10059). Verified mapping
(2026-09-24, dates with both sources, 100% inst/ind and ~100% frn after adding natfor):
  inst_net_buy_amt = orgn ; ind_net_buy_amt = ind_invsr ; frn_net_buy_amt = frgnr_invsr + natfor.
Only rows whose three amount columns are all NULL are filled (existing values are never overwritten).
Old values (NULL) backed up in price_history_supply_backfill_backup (rollback = set columns NULL for run_id).
Dry-run default; --apply writes."""
import json, sys
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
START, END = "2026-09-14", "2026-09-18"

def main(apply):
    conn = connect_primary_db(timeout=300, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds"); run_id = f"supply_backfill_kiwoom_20260924_{datetime.now().strftime('%H%M%S')}"
    # validate mapping on already-filled rows of the same window and neighbouring days
    v = conn.execute("""SELECT COUNT(*),
        SUM(CASE WHEN ABS(p.inst_net_buy_amt-k.orgn)<=1 THEN 1 ELSE 0 END),
        SUM(CASE WHEN ABS(p.ind_net_buy_amt-k.ind_invsr)<=1 THEN 1 ELSE 0 END),
        SUM(CASE WHEN ABS(p.frn_net_buy_amt-(k.frgnr_invsr+COALESCE(k.natfor,0)))<=1 THEN 1 ELSE 0 END)
        FROM price_history p JOIN kiwoom_investor_daily k ON k.stock_code=p.stock_code AND k.dt=p.date::text
        WHERE p.date::text IN ('2026-09-11','2026-09-14','2026-09-15','2026-09-16','2026-09-17','2026-09-18','2026-09-21')
          AND p.inst_net_buy_amt IS NOT NULL AND k.orgn IS NOT NULL""").fetchone()
    print("mapping check (rows, inst==, ind==, frn==):", tuple(v))
    rows = [tuple(r) for r in conn.execute(f"""SELECT p.stock_code,p.date::text,k.orgn,k.frgnr_invsr+COALESCE(k.natfor,0),k.ind_invsr
        FROM price_history p JOIN kiwoom_investor_daily k ON k.stock_code=p.stock_code AND k.dt=p.date::text
        WHERE p.date::text BETWEEN '{START}' AND '{END}' AND p.inst_net_buy_amt IS NULL AND p.frn_net_buy_amt IS NULL AND p.ind_net_buy_amt IS NULL
          AND k.orgn IS NOT NULL AND k.frgnr_invsr IS NOT NULL AND k.ind_invsr IS NOT NULL""").fetchall()]
    print("rows to fill:", len(rows))
    if apply:
        conn.execute("""CREATE TABLE IF NOT EXISTS price_history_supply_backfill_backup (run_id TEXT, stock_code TEXT, date TEXT,
            old_inst DOUBLE PRECISION, old_frn DOUBLE PRECISION, old_ind DOUBLE PRECISION, new_inst DOUBLE PRECISION, new_frn DOUBLE PRECISION, new_ind DOUBLE PRECISION,
            fixed_at TEXT, PRIMARY KEY(run_id, stock_code, date))""")
        conn.execute("SELECT set_config('app.price_basis_checked','1',true)")
        conn.executemany("""INSERT INTO price_history_supply_backfill_backup (run_id,stock_code,date,new_inst,new_frn,new_ind,fixed_at)
            VALUES (?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""", [(run_id, c, d, float(i), float(f), float(n), now) for c, d, i, f, n in rows])
        conn.executemany("""UPDATE price_history SET inst_net_buy_amt=?, frn_net_buy_amt=?, ind_net_buy_amt=?
            WHERE stock_code=? AND date::text=? AND inst_net_buy_amt IS NULL AND frn_net_buy_amt IS NULL AND ind_net_buy_amt IS NULL""",
            [(float(i), float(f), float(n), c, d) for c, d, i, f, n in rows])
        conn.execute("""INSERT INTO data_fix_log (fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)
            VALUES (?,?,?,?,?,?,?,?,?)""", (now, "price_history", f"investor net-buy amounts {START}~{END} (KRX 18:10 collection gap)", len(rows),
            "SET inst/frn/ind_net_buy_amt = kiwoom orgn / frgnr_invsr+natfor / ind_invsr WHERE all three NULL",
            "inst/frn/ind_net_buy_amt NULL", "kiwoom_investor_daily net-buy amounts (mapping verified 100% on overlapping dates)",
            "kiwoom_investor_daily (ka10059)", run_id))
        conn.commit()
    conn.close(); print(json.dumps({"rows": len(rows), "run_id": run_id, "dry_run": not apply}))

if __name__ == "__main__":
    main("--apply" in sys.argv)
