#!/usr/bin/env python3
"""Quarterly (Q1-Q3, 2016-2025Q1) rows unified on the FnGuide basis with OpenDART parent-attributable accounts.
Input /tmp/dart_q_full*.jsonl (scratch dart_q_full_fetch.py: fnlttSinglAcntAll 11013/11012/11014, CFS else OFS; thstrm_amount = 3-month P&L, quarter-end BS).
Per row of the fetched basis (report_type = fs; every data_source variant of the stock-quarter):
  - net_income <- ni_parent (else ni), total_equity <- equity_parent (else equity): converted when off by > max(1e6 won, 0.05%)  [FnGuide basis]
  - revenue / operating_profit / total_assets: only NULL is filled (already reconciled to DART earlier today)
Financial-sector stocks (stock_universe.sector_large contains 금융/보험/은행/증권) get no revenue/operating_profit fill (account definitions differ).
Logs: financial_fix_log (+ fnguide_dart_mismatch_log for conversions). data_lock stock-years: approved unlock -> update -> relock (hash untouched: it hashes the annual row).
Dry-run default; --apply writes."""
import glob, json, sys
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
FILL_ONLY = ("revenue", "operating_profit", "total_assets")
COLS = ("revenue", "operating_profit", "total_assets", "net_income", "total_equity")

def main(apply):
    conn = connect_primary_db(timeout=900, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds"); run_id = f"dart_parent_basis_quarterly_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    locked = {(r[0], r[1]) for r in conn.execute("SELECT stock_code,year FROM data_lock WHERE table_name='financial_data' AND is_locked=1").fetchall()}
    fin = {r[0] for r in conn.execute("SELECT stock_code FROM stock_universe WHERE sector_large LIKE '%금융%' OR sector_large LIKE '%보험%' OR sector_large LIKE '%은행%' OR sector_large LIKE '%증권%'").fetchall()}
    res = {"stock_quarters": 0, "ni_equity_converted": 0, "filled_null": 0, "unchanged": 0}
    seen = {}
    for fp in sorted(glob.glob("/tmp/dart_q_full*.jsonl")):
        for line in open(fp):
            d = json.loads(line)
            if not d.get("err") and d.get("fs"): seen[(d["code"], d["year"], d["quarter"])] = d
    unlocked_set = set()
    for n, ((code, y, q), d) in enumerate(seen.items()):
        if q not in (1, 2, 3) or not 2016 <= y <= 2025 or (y == 2025 and q != 1): continue
        fs = d["fs"]
        tgt = {"revenue": d.get("revenue"), "operating_profit": d.get("op"), "total_assets": d.get("assets"),
               "net_income": d["ni_parent"] if d.get("ni_parent") is not None else d.get("ni"),
               "total_equity": d["equity_parent"] if d.get("equity_parent") is not None else d.get("equity")}
        rows = conn.execute("SELECT id,revenue,operating_profit,total_assets,net_income,total_equity FROM financial_data WHERE stock_code=? AND year=? AND quarter=? AND is_annual=false AND report_type=?", (code, y, q, fs)).fetchall()
        if not rows: continue
        res["stock_quarters"] += 1; is_locked = (code, y) in locked
        for row in rows:
            for i, f in enumerate(COLS):
                L = tgt[f]; D = row[i + 1]
                if L is None: continue
                if f in FILL_ONLY:
                    if D is not None or (f != "total_assets" and code in fin): continue
                    kind = "fill"
                else:
                    if D is not None and abs(D - L) <= max(1e6, abs(L) * 0.0005): res["unchanged"] += 1; continue
                    kind = "fill" if D is None else "convert"
                res["filled_null" if kind == "fill" else "ni_equity_converted"] += 1
                if not apply: continue
                if is_locked and (code, y) not in unlocked_set:
                    conn.execute("UPDATE data_lock SET is_locked=0, unlocked_at=?, unlock_reason=? WHERE stock_code=? AND year=? AND table_name='financial_data'",
                                 (now, f"2026-09-26 user-approved: parent-attributable FnGuide basis quarterly ({run_id})", code, y)); unlocked_set.add((code, y))
                conn.execute(f"UPDATE financial_data SET {f}=?, updated_at=? WHERE id=?", (L, now, row[0]))
                conn.execute("""INSERT INTO financial_fix_log (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                    VALUES (?,?,?,?,?,0,?,?,?,?,?,?,?)""", (now, row[0], code, y, q, fs, f, D, L,
                    "FnGuide basis: net income / equity parent-attributable (OpenDART Attributable-to-owners-of-parent accounts)" if kind == "convert" else "NULL filled from OpenDART quarterly statement",
                    f"OpenDART fnlttSinglAcntAll {y} q{q} {fs}", run_id))
                if kind == "convert":
                    conn.execute("INSERT INTO fnguide_dart_mismatch_log (stock_code,year,field,note,found_at) VALUES (?,?,?,?,?)",
                                 (code, y, f, f"FnGuide기준(지배주주) 전환 {y}Q{q} {fs}: 기존(전체)={D} 지배={L} run={run_id}", now))
        if apply and n % 500 == 499: conn.commit()
    if apply:
        for code, y in unlocked_set:
            conn.execute("UPDATE data_lock SET is_locked=1, locked_at=? WHERE stock_code=? AND year=? AND table_name='financial_data'", (now, code, y))
        conn.commit()
    conn.close(); print({**res, "run_id": run_id, "dry_run": not apply, "keys_used": len(seen)})

if __name__ == "__main__":
    main("--apply" in sys.argv)
