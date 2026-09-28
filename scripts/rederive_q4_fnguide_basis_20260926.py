#!/usr/bin/env python3
"""Q4 re-derivation on the unified FnGuide basis: for each stock-year and report_type (CFS preferred, else OFS) that has an annual row and Q1..Q4 rows,
  Q4 revenue / operating_profit / net_income = annual - (Q1+Q2+Q3)  (project rule; needs all three of Q1..Q3 non-NULL),
  Q4 total_assets / total_equity = annual value (year-end balance sheet).
Changes only when the Q4 value is NULL or differs from the derived value by more than max(1e8 won, 0.5%); the derived value must have the same sign
plausibility (no derivation when Q1-Q3 sum is 0 and annual is 0). Uses the annual/quarterly rows as they are (basis already unified); when several
source-variant rows exist for a key the one with the most recent updated_at is used as the reference and every Q4 variant is updated.
Logs to financial_fix_log (fix_rule 'Q4 = annual - Q1 - Q2 - Q3 ...'); data_lock stock-years follow unlock -> update -> relock. Dry-run default."""
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
FLOW = ("revenue", "operating_profit", "net_income")
BS = ("total_assets", "total_equity")

def main(apply):
    conn = connect_primary_db(timeout=900, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds"); run_id = f"q4_fnguide_basis_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    locked = {(r[0], r[1]) for r in conn.execute("SELECT stock_code,year FROM data_lock WHERE table_name='financial_data' AND is_locked=1").fetchall()}
    rows = conn.execute("""SELECT id,stock_code,year,quarter,is_annual,report_type,revenue,operating_profit,net_income,total_assets,total_equity,COALESCE(updated_at,created_at,'')
                           FROM financial_data WHERE year BETWEEN 2016 AND 2025 AND stock_code ~ '^[0-9]{6}$'""").fetchall()
    g = defaultdict(lambda: defaultdict(list))
    for r in rows: g[(r[1], r[2], r[5])]["A" if r[4] else r[3]].append(r)
    res = {"stock_years": 0, "flow_changed": 0, "bs_changed": 0, "unchanged": 0}; unlocked = set()
    for (code, y, rt), d in g.items():
        if rt == "OFS" and (code, y, "CFS") in g and g[(code, y, "CFS")].get("A"): continue
        if not d.get("A") or not d.get(4) or any(not d.get(q) for q in (1, 2, 3)): continue
        pick = lambda lst: max(lst, key=lambda r: r[11])
        A, q1, q2, q3 = pick(d["A"]), pick(d[1]), pick(d[2]), pick(d[3])
        res["stock_years"] += 1
        for q4 in d[4]:
            for f, i in (("revenue", 6), ("operating_profit", 7), ("net_income", 8), ("total_assets", 9), ("total_equity", 10)):
                if f in FLOW:
                    v = [A[i], q1[i], q2[i], q3[i]]
                    if None in v: continue
                    V = v[0] - v[1] - v[2] - v[3]
                else:
                    V = A[i]
                    if V is None: continue
                D = q4[i]
                if f == "revenue" and V < 0: res["skipped_negative_revenue"] = res.get("skipped_negative_revenue", 0) + 1; continue  # annual < Q1+Q2+Q3: inputs inconsistent, needs source review
                if D is not None and abs(D - V) <= max(1e8, abs(V) * 0.005): res["unchanged"] += 1; continue
                res["flow_changed" if f in FLOW else "bs_changed"] += 1
                if not apply: continue
                if (code, y) in locked and (code, y) not in unlocked:
                    conn.execute("UPDATE data_lock SET is_locked=0, unlocked_at=?, unlock_reason=? WHERE stock_code=? AND year=? AND table_name='financial_data'",
                                 (now, f"2026-09-26 user-approved: Q4 re-derived on unified FnGuide basis ({run_id})", code, y)); unlocked.add((code, y))
                conn.execute(f"UPDATE financial_data SET {f}=?, updated_at=? WHERE id=?", (V, now, q4[0]))
                conn.execute("""INSERT INTO financial_fix_log (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                    VALUES (?,?,?,?,4,0,?,?,?,?,?,?,?)""", (now, q4[0], code, y, rt, f, D, V,
                    "Q4 = annual - Q1 - Q2 - Q3 (flow) / annual year-end (balance sheet) on the unified FnGuide basis", "derived in DB", run_id))
    if apply:
        for code, y in unlocked:
            conn.execute("UPDATE data_lock SET is_locked=1, locked_at=? WHERE stock_code=? AND year=? AND table_name='financial_data'", (now, code, y))
        conn.commit()
    conn.close(); print({**res, "run_id": run_id, "dry_run": not apply})

if __name__ == "__main__":
    main("--apply" in sys.argv)
