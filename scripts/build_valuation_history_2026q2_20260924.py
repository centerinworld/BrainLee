#!/usr/bin/env python3
"""Add the missing 2026 Q2 (period_end 2026-06-30) rows to valuation_history (table had nothing after 2026-03-31 and no
producer script exists in the repo). Only fields whose formula was reproduced from the existing rows are filled:
  close_price = price_history close on the last trading day <= 2026-06-30 (within 5 days)   [matches existing rows 99%]
  shares_issued = stock_universe.shares_issued (latest)                                       [1,771/2,574 identical; others older shares]
  market_cap_억 = close * shares_issued / 1e8                                                  [100% on 2026Q1]
  bps  = OFS quarterly bps else OFS equity/shares else CFS bps else CFS equity/shares         [97% on 2026Q1, 97% on 2025Q4]
  pbr  = close / bps (4 decimals)                                                              [100% given bps]
eps/per are left NULL: the eps definition changes between historical snapshots (quarterly EPS vs NI/shares, match rate
14%..50% per quarter) and cannot be reproduced without guessing. data_source='calculated_partial_20260924'.
Insert-only (ON CONFLICT DO NOTHING). Dry-run default."""
import json, sys
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

def main(apply):
    conn = connect_primary_db(timeout=600, readonly=not apply)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S"); run_id = f"valuation_history_2026q2_{datetime.now().strftime('%H%M%S')}"
    codes = [r[0] for r in conn.execute("SELECT stock_code FROM valuation_history WHERE year=2026 AND quarter=1").fetchall()]
    shares = {r[0]: r[1] for r in conn.execute("SELECT DISTINCT ON (stock_code) stock_code,shares_issued FROM stock_universe ORDER BY stock_code,base_date DESC").fetchall()}
    px = {}
    for code, d, cl in conn.execute("""SELECT stock_code,date::text,close FROM price_history WHERE date::text BETWEEN '2026-06-24' AND '2026-06-30' AND close>0 ORDER BY date""").fetchall():
        px[code] = float(cl)
    fin = {}
    for code, rt, eq, bps in conn.execute("SELECT stock_code,report_type,total_equity,bps FROM financial_data WHERE year=2026 AND quarter=2 AND is_annual IS FALSE").fetchall():
        fin.setdefault(code, {})[rt] = (eq, bps)
    rows = []
    for code in codes:
        cl, sh = px.get(code), shares.get(code)
        if cl is None: continue
        sh = float(sh) if sh else None
        bps = None
        f = fin.get(code, {}); ofs, cfs = f.get("OFS"), f.get("CFS")
        if ofs and ofs[1] is not None: bps = float(ofs[1])
        elif ofs and ofs[0] is not None and sh: bps = float(ofs[0]) / sh
        elif cfs and cfs[1] is not None: bps = float(cfs[1])
        elif cfs and cfs[0] is not None and sh: bps = float(cfs[0]) / sh
        pbr = round(cl / bps, 4) if bps and bps > 0 else None
        mc = round(cl * sh / 1e8, 1) if sh else None
        rows.append((code, 2026, 2, "2026-06-30", cl, None, bps, None, pbr, mc, sh, "calculated_partial_20260924", now))
    print("rows:", len(rows), "with bps:", sum(1 for r in rows if r[6] is not None), "with pbr:", sum(1 for r in rows if r[8] is not None), "sample", rows[:2])
    if apply and rows:
        nid = conn.execute("SELECT COALESCE(MAX(id),0) FROM valuation_history").fetchone()[0]
        conn.executemany("""INSERT INTO valuation_history (id,stock_code,year,quarter,period_end,close_price,eps,bps,per,pbr,market_cap_억,shares_issued,data_source,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""", [(nid + i + 1, *r) for i, r in enumerate(rows)])
        conn.execute("""INSERT INTO data_fix_log (fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)
            VALUES (?,?,?,?,?,?,?,?,?)""", (now.replace(" ", "T"), "valuation_history", "2026 Q2 rows (table stopped after 2026-03-31)", len(rows),
            "INSERT rows with close/shares/market cap/bps/pbr reproduced from existing formula; eps/per NULL", "no 2026Q2 rows", "data_source=calculated_partial_20260924",
            "price_history + stock_universe + financial_data", run_id))
        conn.commit()
    conn.close(); print(json.dumps({"rows": len(rows), "run_id": run_id, "dry_run": not apply}))

if __name__ == "__main__":
    main("--apply" in sys.argv)
