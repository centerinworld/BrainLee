#!/usr/bin/env python3
"""Revert every FnGuide/Naver write into cash_flow_data made on 2026-09-26 (owner rule: DART API is the ONLY write path for cash flow; FnGuide/Naver reference only).
1) Cells: financial_fix_log entries 'cash_flow_data.<col>' whose source starts with 'Naver' (run fnguide_basis_cf_annual_*). Per cell: restore the FIRST old_value of the
   family (NULL for fills) only if the LAST entry of the cell is Naver-sourced and the current value still equals that last new_value (a later DART correction is kept).
2) Rows inserted by that run (cash_flow_data.data_source = 'fnguide_naver'): deleted after copying them to cash_flow_data_deleted_fnguide_naver_20260926.
Logs each restored cell to financial_fix_log (run_id printed). Dry-run default; --apply writes."""
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

def main(apply):
    conn = connect_primary_db(timeout=900, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds"); run_id = f"revert_cf_naver_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    logs = conn.execute("""SELECT id,row_id,stock_code,year,report_type,field_name,old_value,new_value,source FROM financial_fix_log
                           WHERE field_name LIKE 'cash_flow_data.%' AND run_id LIKE 'fnguide_basis_cf_annual_%' ORDER BY id""").fetchall()
    cells = defaultdict(list)
    for r in logs: cells[(r[1], r[5])].append(r)
    todo = []; res = {"cells": len(cells), "restore": 0, "restore_to_null": 0, "skip_last_is_dart": 0, "skip_changed_since": 0, "skip_already": 0}
    for (rid, field), ents in cells.items():
        if not (ents[-1][8] or "").startswith("Naver"): res["skip_last_is_dart"] += 1; continue
        first, last = ents[0], ents[-1]; col = field.split(".", 1)[1]
        cur = conn.execute(f"SELECT {col} FROM cash_flow_data WHERE id=?", (rid,)).fetchone()
        if cur is None: continue
        cur = cur[0]
        if cur is None and first[6] is None: res["skip_already"] += 1; continue
        if cur is not None and first[6] is not None and abs(cur - first[6]) <= 1e-6: res["skip_already"] += 1; continue
        if cur is None or abs(cur - last[7]) > max(1.0, abs(last[7]) * 1e-9): res["skip_changed_since"] += 1; continue
        res["restore_to_null" if first[6] is None else "restore"] += 1; todo.append((rid, col, first, cur))
    ins = conn.execute("SELECT COUNT(*) FROM cash_flow_data WHERE data_source='fnguide_naver'").fetchone()[0]
    res["inserted_rows_to_delete"] = ins
    if apply:
        for rid, col, first, cur in todo:
            conn.execute(f"UPDATE cash_flow_data SET {col}=?, updated_at=? WHERE id=?", (first[6], now, rid))
            conn.execute("""INSERT INTO financial_fix_log (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                VALUES (?,?,?,?,0,1,?,?,?,?,?,?,?)""", (now, rid, first[2], first[3], first[4], first[5], cur, first[6],
                "revert: FnGuide/Naver value removed from cash_flow_data (DART is the only write path for cash flow)", "financial_fix_log first old_value", run_id))
        conn.execute("DROP TABLE IF EXISTS cash_flow_data_deleted_fnguide_naver_20260926")
        conn.execute("CREATE TABLE cash_flow_data_deleted_fnguide_naver_20260926 AS SELECT * FROM cash_flow_data WHERE data_source='fnguide_naver'")
        conn.execute("DELETE FROM cash_flow_data WHERE data_source='fnguide_naver'")
        conn.commit()
    conn.close(); print({**res, "run_id": run_id, "dry_run": not apply})

if __name__ == "__main__":
    main("--apply" in sys.argv)
