#!/usr/bin/env python3
"""Fill remaining NULL core fields (annual and Q1-Q3, 2016-2025) from OpenDART re-fetches with account_id + account_nm fallbacks
(/tmp/dart_null_fill.jsonl, scratch dart_null_fill_fetch.py). NULL cells only - existing values are never changed.
FnGuide basis: net_income <- ni_parent (else ni), total_equity <- equity_parent (else equity). Target rows: rows of the stock-period whose report_type equals
the fetched basis; when only the OFS statement exists at DART (no CFS filed) the CFS-labelled rows of that stock-period are filled from it too
(those stocks file separate statements only; the CFS label is a legacy mislabel). Logs to financial_fix_log (old NULL). data_lock stock-years:
unlock -> update -> relock (hash recomputed for annual rows). Dry-run default; --apply writes."""
import hashlib, json, sys
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
COLS = ("revenue", "operating_profit", "net_income", "total_assets", "total_equity")

def main(apply):
    conn = connect_primary_db(timeout=900, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds"); run_id = f"dart_null_fill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    locked = {(r[0], r[1]) for r in conn.execute("SELECT stock_code,year FROM data_lock WHERE table_name='financial_data' AND is_locked=1").fetchall()}
    res = {"periods": 0, "cells_filled": 0}; by = {}; unlocked = set(); annual_touched = set()
    for n, line in enumerate(open("/tmp/dart_null_fill.jsonl")):
        d = json.loads(line)
        if d.get("err") or not d.get("fs"): continue
        code, y, q, fs = d["code"], d["year"], d["quarter"], d["fs"]
        tgt = {"revenue": d.get("revenue"), "operating_profit": d.get("op"),
               "net_income": d["ni_parent"] if d.get("ni_parent") is not None else d.get("ni"),
               "total_assets": d.get("assets"),
               "total_equity": d["equity_parent"] if d.get("equity_parent") is not None else d.get("equity")}
        is_ann = q == 0
        rows = conn.execute("SELECT id,report_type,revenue,operating_profit,net_income,total_assets,total_equity FROM financial_data WHERE stock_code=? AND year=? AND is_annual=? AND (quarter=? OR ?)",
                            (code, y, is_ann, q, is_ann)).fetchall()
        use = [r for r in rows if r[1] == fs]
        if fs == "OFS": use += [r for r in rows if r[1] == "CFS"]
        if not use: continue
        res["periods"] += 1
        for row in use:
            for i, f in enumerate(COLS):
                L = tgt[f]
                if L is None or row[i + 2] is not None: continue
                res["cells_filled"] += 1; by[f] = by.get(f, 0) + 1
                if not apply: continue
                if (code, y) in locked and (code, y) not in unlocked:
                    conn.execute("UPDATE data_lock SET is_locked=0, unlocked_at=?, unlock_reason=? WHERE stock_code=? AND year=? AND table_name='financial_data'",
                                 (now, f"2026-09-26 user-approved: NULL fill from OpenDART ({run_id})", code, y)); unlocked.add((code, y))
                conn.execute(f"UPDATE financial_data SET {f}=?, updated_at=? WHERE id=? AND {f} IS NULL", (L, now, row[0]))
                conn.execute("""INSERT INTO financial_fix_log (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                    VALUES (?,?,?,?,?,?,?,?,NULL,?,?,?,?)""", (now, row[0], code, y, q, 1 if is_ann else 0, row[1], f, L,
                    "NULL filled from OpenDART statement (account_id / account_nm fallback); FnGuide basis for net income / equity", f"OpenDART fnlttSinglAcntAll {y} {fs}", run_id))
                if is_ann: annual_touched.add((code, y, row[1]))
        if apply and n % 500 == 499: conn.commit()
    if apply:
        for code, y in unlocked:
            h = conn.execute("SELECT revenue,operating_profit,net_income FROM financial_data WHERE stock_code=? AND year=? AND is_annual=true ORDER BY (CASE WHEN report_type='CFS' THEN 0 ELSE 1 END),(CASE WHEN data_source LIKE 'dart%' THEN 0 ELSE 1 END),id LIMIT 1", (code, y)).fetchone()
            new_hash = hashlib.md5(f"{h[0]}|{h[1]}|{h[2]}".encode()).hexdigest() if h and (code, y, "CFS") in annual_touched or (h and (code, y, "OFS") in annual_touched) else None
            conn.execute("UPDATE data_lock SET is_locked=1, locked_at=?, lock_hash=COALESCE(?, lock_hash) WHERE stock_code=? AND year=? AND table_name='financial_data'", (now, new_hash, code, y))
        conn.commit()
    conn.close(); print({**res, "by_field": by, "run_id": run_id, "dry_run": not apply})

if __name__ == "__main__":
    main("--apply" in sys.argv)
