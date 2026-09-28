#!/usr/bin/env python3
"""FnGuide-basis reconciliation for one quarter (default 2026 Q2), per user decision 2026-09-26: "FnGuide 기준으로 진행하고, DART와 다르면 표시".
Reads the newest FnGuide snapshot (financial_source_snapshot, data_source='fnguide', CFS preferred, else OFS) and, for revenue / operating_profit /
net_income (FnGuide publishes only these quarterly; quarterly balance-sheet items are not available from FnGuide):
  - same within rounding (|diff| <= 1e8 won or 0.5%): DB row kept (the DART value is more precise), nothing written;
  - different: financial_data.<field> is set to the FnGuide value, the old (DART-side) value is recorded in financial_fix_log
    (rule 'FnGuide-basis: differs from DART') and in fnguide_dart_mismatch_log (note holds quarter, DART value, FnGuide value, ratio);
  - no DB row for that stock-quarter: a new row is inserted with data_source='fnguide'.
Dry-run default; --apply writes. run_id printed. Old-value guard; stock-years locked in data_lock follow the approved unlock->update->relock."""
import sys
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
YEAR = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 2026
QTR = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 2
FIELDS = ("revenue", "operating_profit", "net_income")

def same(a, b):
    d = abs(a - b)
    return d <= 1e8 or d <= abs(b) * 0.005

def main(apply):
    conn = connect_primary_db(timeout=900, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds"); run_id = f"fnguide_basis_{YEAR}q{QTR}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    snaps = {}
    for r in conn.execute("""SELECT DISTINCT ON (stock_code, report_type) stock_code, report_type, revenue, operating_profit, net_income
        FROM financial_source_snapshot WHERE data_source='fnguide' AND year=? AND quarter=? AND is_annual=0
        ORDER BY stock_code, report_type, fetched_at DESC""", (YEAR, QTR)).fetchall():
        snaps.setdefault(r[0], {})[r[1]] = dict(zip(FIELDS, r[2:]))
    locked = {(r[0], r[1]) for r in conn.execute("SELECT stock_code,year FROM data_lock WHERE table_name='financial_data' AND is_locked=1").fetchall()}
    res = {"stocks": len(snaps), "kept_same": 0, "overwritten": 0, "inserted": 0, "no_fg_value": 0}
    for code, byrt in snaps.items():
        rt = "CFS" if "CFS" in byrt else "OFS"; fg = byrt[rt]
        rows = conn.execute("SELECT id,revenue,operating_profit,net_income FROM financial_data WHERE stock_code=? AND year=? AND quarter=? AND is_annual=0 AND report_type=?",
                            (code, YEAR, QTR, rt)).fetchall()
        if len(rows) > 1: continue
        if not rows:
            if any(fg[f] is not None for f in FIELDS):
                res["inserted"] += 1
                if apply:
                    conn.execute("""INSERT INTO financial_data (id,stock_code,year,quarter,is_annual,report_type,revenue,operating_profit,net_income,data_source,updated_at)
                        VALUES ((SELECT COALESCE(MAX(id),0)+1 FROM financial_data),?,?,?,false,?,?,?,?,?,?)""",
                        (code, YEAR, QTR, rt, fg["revenue"], fg["operating_profit"], fg["net_income"], "fnguide", now))
            continue
        row = rows[0]; is_locked = (code, YEAR) in locked; unlocked = False
        for i, f in enumerate(FIELDS):
            L = fg[f]; D = row[i + 1]
            if L is None: res["no_fg_value"] += 1; continue
            if D is not None and same(D, L): res["kept_same"] += 1; continue
            res["overwritten"] += 1
            if not apply: continue
            if is_locked and not unlocked:
                conn.execute("UPDATE data_lock SET is_locked=0, unlocked_at=?, unlock_reason=? WHERE stock_code=? AND year=? AND table_name='financial_data'",
                             (now, f"2026-09-26 user-approved: FnGuide-basis {YEAR}Q{QTR} ({run_id})", code, YEAR)); unlocked = True
            conn.execute(f"UPDATE financial_data SET {f}=?, data_source=COALESCE(data_source,'')||'+fnguide_basis', updated_at=? WHERE id=?", (L, now, row[0]))
            conn.execute("""INSERT INTO financial_fix_log (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                VALUES (?,?,?,?,?,0,?,?,?,?,?,?,?)""", (now, row[0], code, YEAR, QTR, rt, f, D, L,
                "FnGuide-basis: differs from DART/previous value; FnGuide adopted per user decision", "FnGuide wcomp getFinIncome quarterly snapshot", run_id))
            ratio = (abs(D - L) / max(abs(L), 1)) * 100 if D is not None else None
            conn.execute("INSERT INTO fnguide_dart_mismatch_log (stock_code,year,field,note,found_at) VALUES (?,?,?,?,?)",
                         (code, YEAR, f, f"FnGuide기준 채택 {YEAR}Q{QTR} {rt}: DART/기존={D} FnG={L}" + (f" ({ratio:.1f}%차)" if ratio is not None else " (기존값 없음)") + f" run={run_id}", now))
        if apply and unlocked:
            conn.execute("UPDATE data_lock SET is_locked=1, locked_at=? WHERE stock_code=? AND year=? AND table_name='financial_data'", (now, code, YEAR))
    if apply: conn.commit()
    conn.close(); print({**res, "run_id": run_id, "dry_run": not apply})

if __name__ == "__main__":
    main("--apply" in sys.argv)
