#!/usr/bin/env python3
"""Annual 2016-2021 rows: unify on the FnGuide basis using OpenDART parent-attributable accounts (Naver/FnGuide statements do not reach
before 2021). Input /tmp/dart_a_full.jsonl (scratch dart_a_full_fetch.py: fnlttSinglAcntAll 11011, CFS else OFS; ifrs-full_ProfitLoss(+AttributableToOwnersOfParent),
Equity(+AttributableToOwnersOfParent), Assets, Revenue, OperatingIncomeLoss). Rules per stock-year on the rows of the fetched basis (CFS/OFS):
  - net_income  <- ni_parent (else ni);  total_equity <- equity_parent (else equity): overwritten when off by > max(1e6 won, 0.05%)
    (this is the basis conversion, logged as FnGuide basis; not a data error);
  - revenue / operating_profit / total_assets: only NULL is filled (those were already reconciled to DART earlier);
  - a missing annual row is not created here.
Every change -> financial_fix_log + fnguide_dart_mismatch_log (only when the old value was non-NULL). data_lock stock-years: approved unlock -> update -> relock
with lock_hash = md5('revenue|operating_profit|net_income'). Dry-run default; --apply writes."""
import hashlib, json, sys
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
FILL_ONLY = ("revenue", "operating_profit", "total_assets")

def main(apply):
    conn = connect_primary_db(timeout=900, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds"); run_id = f"dart_parent_basis_annual_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    locked = {(r[0], r[1]) for r in conn.execute("SELECT stock_code,year FROM data_lock WHERE table_name='financial_data' AND is_locked=1").fetchall()}
    res = {"stock_years": 0, "ni_equity_converted": 0, "filled_null": 0, "unchanged": 0}
    for n, line in enumerate(open("/tmp/dart_a_full.jsonl")):
        d = json.loads(line)
        if d.get("err") or not d.get("fs") or not 2016 <= d["year"] <= 2021: continue
        code, y, fs = d["code"], d["year"], d["fs"]
        tgt = {"revenue": d.get("revenue"), "operating_profit": d.get("op"), "total_assets": d.get("assets"),
               "net_income": d["ni_parent"] if d.get("ni_parent") is not None else d.get("ni"),
               "total_equity": d["equity_parent"] if d.get("equity_parent") is not None else d.get("equity")}
        rows = conn.execute("SELECT id,revenue,operating_profit,total_assets,net_income,total_equity FROM financial_data WHERE stock_code=? AND year=? AND is_annual=true AND report_type=?", (code, y, fs)).fetchall()
        if not rows: continue
        res["stock_years"] += 1; is_locked = (code, y) in locked; unlocked = False
        cols = ("revenue", "operating_profit", "total_assets", "net_income", "total_equity")
        for row in rows:
            for i, f in enumerate(cols):
                L = tgt[f]; D = row[i + 1]
                if L is None: continue
                if f in FILL_ONLY:
                    if D is not None: continue
                    kind = "fill"
                else:
                    if D is not None and abs(D - L) <= max(1e6, abs(L) * 0.0005): res["unchanged"] += 1; continue
                    kind = "fill" if D is None else "convert"
                res["filled_null" if kind == "fill" else "ni_equity_converted"] += 1
                if not apply: continue
                if is_locked and not unlocked:
                    conn.execute("UPDATE data_lock SET is_locked=0, unlocked_at=?, unlock_reason=? WHERE stock_code=? AND year=? AND table_name='financial_data'",
                                 (now, f"2026-09-26 user-approved: parent-attributable FnGuide basis annual ({run_id})", code, y)); unlocked = True
                conn.execute(f"UPDATE financial_data SET {f}=?, updated_at=? WHERE id=?", (L, now, row[0]))
                conn.execute("""INSERT INTO financial_fix_log (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                    VALUES (?,?,?,?,0,1,?,?,?,?,?,?,?)""", (now, row[0], code, y, fs, f, D, L,
                    "FnGuide basis: net income / equity parent-attributable (OpenDART Attributable-to-owners-of-parent accounts)" if kind == "convert" else "NULL filled from OpenDART annual statement",
                    f"OpenDART fnlttSinglAcntAll {y} 11011 {fs}", run_id))
                if kind == "convert":
                    conn.execute("INSERT INTO fnguide_dart_mismatch_log (stock_code,year,field,note,found_at) VALUES (?,?,?,?,?)",
                                 (code, y, f, f"FnGuide기준(지배주주) 전환 {fs}: 기존(전체)={D} 지배={L} run={run_id}", now))
        if apply and unlocked:
            h = conn.execute("SELECT revenue,operating_profit,net_income FROM financial_data WHERE stock_code=? AND year=? AND is_annual=true AND report_type=? ORDER BY (CASE WHEN data_source LIKE 'dart%' THEN 0 ELSE 1 END), id LIMIT 1", (code, y, fs)).fetchone()
            new_hash = hashlib.md5(f"{h[0]}|{h[1]}|{h[2]}".encode()).hexdigest() if h else None
            conn.execute("UPDATE data_lock SET is_locked=1, locked_at=?, lock_hash=COALESCE(?, lock_hash) WHERE stock_code=? AND year=? AND table_name='financial_data'", (now, new_hash, code, y))
        if apply and n % 300 == 299: conn.commit()
    if apply: conn.commit()
    conn.close(); print({**res, "run_id": run_id, "dry_run": not apply})

if __name__ == "__main__":
    main("--apply" in sys.argv)
