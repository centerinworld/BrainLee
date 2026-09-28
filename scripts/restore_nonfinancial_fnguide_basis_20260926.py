#!/usr/bin/env python3
"""Restore NON-financial stocks to their values from before the 2026-09-26 "FnGuide basis" passes (user decision: FnGuide basis only for the
problematic financial sector; the rest stays DART-based). Uses financial_fix_log (old_value/new_value) of the runs
  fnguide_basis_annual_*, fnguide_basis_naver_*, dart_parent_basis_annual_*, dart_parent_basis_quarterly_*, q4_fnguide_basis_* (incl. *_revert), fnguide_basis_cf_annual_*.
Per (table, row_id, field): pre-session value = old_value of the FIRST log entry of these runs; the cell is restored only when its current value equals the
NEW value of the LAST such entry (nothing else changed it since) and the first old_value is not NULL (cells that were NULL and got filled are left as filled).
Financial-sector stocks (stock_universe.sector_large contains 금융/보험/은행/증권) are NOT touched. cash_flow_data rows are logged with field_name 'cash_flow_data.<col>'.
Writes a 'restore' entry to financial_fix_log per restored cell (run_id printed), annotates the matching fnguide_dart_mismatch_log notes ('[복원: 비금융 원복]'),
and follows unlock -> update -> relock for data_lock stock-years (annual rows: lock_hash recomputed). Dry-run default; --apply writes."""
import hashlib, sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
FAMS = ("fnguide_basis_annual_%", "fnguide_basis_naver_%", "dart_parent_basis_annual_%", "dart_parent_basis_quarterly_%", "q4_fnguide_basis_%", "fnguide_basis_cf_annual_%")

def main(apply):
    conn = connect_primary_db(timeout=1800, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds"); run_id = f"restore_nonfinancial_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    fin = {r[0] for r in conn.execute("SELECT stock_code FROM stock_universe WHERE sector_large LIKE '%금융%' OR sector_large LIKE '%보험%' OR sector_large LIKE '%은행%' OR sector_large LIKE '%증권%'").fetchall()}
    locked = {(r[0], r[1]) for r in conn.execute("SELECT stock_code,year FROM data_lock WHERE table_name='financial_data' AND is_locked=1").fetchall()}
    where = " OR ".join(["run_id LIKE ?"] * len(FAMS))
    logs = conn.execute(f"SELECT id,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,run_id FROM financial_fix_log WHERE ({where}) ORDER BY id", FAMS).fetchall()
    cells = defaultdict(list)
    for r in logs:
        if r[2] in fin: continue
        cells[(r[7].startswith("cash_flow_data."), r[1], r[7])].append(r)
    res = {"cells_seen": len(cells), "restored": 0, "skip_first_old_null": 0, "skip_changed_since": 0, "skip_already_original": 0}
    by = defaultdict(int); todo = []
    for (is_cf, rid, field), ents in cells.items():
        first, last = ents[0], ents[-1]
        if first[8] is None: res["skip_first_old_null"] += 1; continue
        tbl, col = ("cash_flow_data", field.split(".", 1)[1]) if is_cf else ("financial_data", field)
        cur = conn.execute(f"SELECT {col} FROM {tbl} WHERE id=?", (rid,)).fetchone()
        if cur is None: continue
        cur = cur[0]
        if cur is not None and abs(cur - first[8]) <= 1e-6: res["skip_already_original"] += 1; continue
        if cur is None or last[9] is None or abs(cur - last[9]) > max(1.0, abs(last[9]) * 1e-9): res["skip_changed_since"] += 1; continue
        res["restored"] += 1; by[tbl + "." + col] += 1
        todo.append((tbl, col, rid, first, cur))
    if apply:
        unlocked = set(); annual = set()
        for tbl, col, rid, first, cur in todo:
            code, y = first[2], first[3]
            if tbl == "financial_data" and (code, y) in locked and (code, y) not in unlocked:
                conn.execute("UPDATE data_lock SET is_locked=0, unlocked_at=?, unlock_reason=? WHERE stock_code=? AND year=? AND table_name='financial_data'",
                             (now, f"2026-09-26 user-approved: restore non-financial to pre-FnGuide-basis values ({run_id})", code, y)); unlocked.add((code, y))
            conn.execute(f"UPDATE {tbl} SET {col}=?, updated_at=? WHERE id=?", (first[8], now, rid))
            conn.execute("""INSERT INTO financial_fix_log (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (now, rid, code, y, first[4], first[5], first[6], first[7] if tbl == "cash_flow_data" else col, cur, first[8],
                "restore: non-financial stock returned to its pre-FnGuide-basis value (FnGuide basis is for the financial sector only)", "financial_fix_log first old_value", run_id))
            if tbl == "financial_data" and first[5]: annual.add((code, y))
        for code, y in unlocked:
            h = conn.execute("SELECT revenue,operating_profit,net_income FROM financial_data WHERE stock_code=? AND year=? AND is_annual=true ORDER BY (CASE WHEN report_type='CFS' THEN 0 ELSE 1 END),(CASE WHEN data_source LIKE 'dart%' THEN 0 ELSE 1 END),id LIMIT 1", (code, y)).fetchone()
            new_hash = hashlib.md5(f"{h[0]}|{h[1]}|{h[2]}".encode()).hexdigest() if h and (code, y) in annual else None
            conn.execute("UPDATE data_lock SET is_locked=1, locked_at=?, lock_hash=COALESCE(?, lock_hash) WHERE stock_code=? AND year=? AND table_name='financial_data'", (now, new_hash, code, y))
        conn.execute("""UPDATE fnguide_dart_mismatch_log SET note = note || ' [복원: 비금융 원복 2026-09-26]'
                        WHERE (note LIKE '%fnguide_basis_%' OR note LIKE '%dart_parent_basis_%') AND note NOT LIKE '%복원: 비금융%'
                          AND stock_code NOT IN (SELECT stock_code FROM stock_universe WHERE sector_large LIKE '%금융%' OR sector_large LIKE '%보험%' OR sector_large LIKE '%은행%' OR sector_large LIKE '%증권%')""")
        conn.commit()
    conn.close(); print({**res, "by_column": dict(by), "run_id": run_id, "dry_run": not apply})

if __name__ == "__main__":
    main("--apply" in sys.argv)
