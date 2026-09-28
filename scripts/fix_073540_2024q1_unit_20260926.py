#!/usr/bin/env python3
"""073540 (2024 Q1): the company's own DART filing carries values ~1000x too large (thousand-won figures labelled won; live OpenDART 자산총계
75,821,267,725,000 vs 2023 annual 78.58bn). Scale the affected value fields by 1/1000 on the CFS+OFS quarterly rows; CFS total_liabilities is a
ratio-fill artefact (>= assets) and is set NULL. Old-value guard + financial_fix_log. Dry-run default."""
import sys
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
SCALE = {217151: ["revenue", "operating_profit", "net_income", "total_assets"], 223319: ["revenue", "operating_profit", "net_income", "total_assets", "total_equity"]}
# P&L rows (op/NI) are only ~1e11 while still x1000; revenue/assets/equity were already rescaled (<1e12) in the first pass
LIMIT = {"operating_profit": 1e11, "net_income": 1e11}
NULLIFY = {217151: ["total_liabilities"]}

def main(apply):
    conn = connect_primary_db(timeout=300, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds"); run_id = f"unit_fix_073540_20260926_{datetime.now().strftime('%H%M%S')}"
    locked = conn.execute("SELECT is_locked FROM data_lock WHERE stock_code='073540' AND year=2024 AND table_name='financial_data'").fetchone()
    n = 0
    for id_, fields in SCALE.items():
        for f in fields:
            old = conn.execute(f"SELECT {f} FROM financial_data WHERE id=?", (id_,)).fetchone()[0]
            if old is None or abs(old) < LIMIT.get(f, 1e12): continue
            new = old / 1000.0; n += 1
            if apply:
                conn.execute(f"UPDATE financial_data SET {f}=? WHERE id=?", (new, id_))
                conn.execute("""INSERT INTO financial_fix_log (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                    VALUES (?,?,'073540',2024,1,0,?,?,?,?,?,?,?)""", (now, id_, "CFS" if id_ == 217151 else "OFS", f, old, new,
                    "company filing carries thousand-won figures labelled won (x1000); scaled by 1/1000", "OpenDART fnlttSinglAcntAll 2024Q1 vs 2023/2024 annual", run_id))
    for id_, fields in NULLIFY.items():
        for f in fields:
            old = conn.execute(f"SELECT {f} FROM financial_data WHERE id=?", (id_,)).fetchone()[0]
            if old is None: continue
            n += 1
            if apply:
                conn.execute(f"UPDATE financial_data SET {f}=NULL WHERE id=?", (id_,))
                conn.execute("""INSERT INTO financial_fix_log (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                    VALUES (?,?,'073540',2024,1,0,'CFS',?,?,NULL,?,?,?)""", (now, id_, f, old, "ratio-fill artefact (liabilities ~ assets x1000 scale); NULLed", "derived", run_id))
    if apply: conn.commit()
    conn.close(); print({"changes": n, "locked_row": tuple(locked) if locked else None, "run_id": run_id, "dry_run": not apply})

if __name__ == "__main__":
    main("--apply" in sys.argv)
