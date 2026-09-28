#!/usr/bin/env python3
"""FnGuide-basis reconciliation for one quarter using Naver's FnGuide-family quarterly statements (FinanceDataReader SnapDataReader
'NAVER/FINSTATE-Q/<code>', 억원, /tmp/fdr_finstate_q.jsonl as fetched by the session scratch fdr_q_fetch.py), per user decision 2026-09-26:
"FnGuide 기준으로 진행하고, DART와 다르면 표시". Fields: revenue, operating_profit, net_income, total_assets, total_equity.
Comparison per field: the DB value is "same" if it matches the FnGuide value within rounding (|diff| <= 1e8 won or 0.5%). Since the
2026-09-26 decision to unify everything on the FnGuide basis, net_income and total_equity are compared with the parent-attributable
("(지배)") value (a total-basis DB value that differs is therefore converted). Otherwise the FnGuide value (parent-attributable for NI/equity, as the FnGuide collector does) is
written, the old value goes to financial_fix_log and to fnguide_dart_mismatch_log (note: quarter, DART/previous value, FnGuide value, ratio).
Missing stock-quarter rows are inserted (report_type = CFS if the stock has a 2025 annual CFS row else OFS; data_source 'fnguide_naver').
Dry-run default; --apply writes. Locked stock-years follow the approved unlock -> update -> relock procedure."""
import json, sys
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
# targets: "python script.py Q" (2026 Q) or "python script.py all" = every quarter Naver holds (2024Q3..2026Q2); Q4 rows are fill-NULL-only
# (project rule Q4 = annual - Q1 - Q2 - Q3 stays authoritative).
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
if ARGS and ARGS[0] == "all":
    TARGETS = [(2024, 3), (2024, 4), (2025, 1), (2025, 2), (2025, 3), (2025, 4), (2026, 1), (2026, 2)]
else:
    TARGETS = [(2026, int(ARGS[0]) if ARGS else 2)]
YEAR = QTR = PERIOD = None
FILL_ONLY = False
MAP = {"revenue": ("매출액", None), "operating_profit": ("영업이익", None), "net_income": ("당기순이익", "당기순이익(지배)"),
       "total_assets": ("자산총계", None), "total_equity": ("자본총계", "자본총계(지배)")}

def same(a, b):
    d = abs(a - b)
    return d <= 1e8 or d <= abs(b) * 0.005

def main(apply):
    global YEAR, QTR, PERIOD, FILL_ONLY
    for YEAR, QTR in TARGETS:
        PERIOD = f"{YEAR}-{QTR * 3:02d}-01"; FILL_ONLY = QTR == 4
        one(apply)

def one(apply):
    conn = connect_primary_db(timeout=900, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds"); run_id = f"fnguide_basis_naver_{YEAR}q{QTR}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    locked = {(r[0], r[1]) for r in conn.execute("SELECT stock_code,year FROM data_lock WHERE table_name='financial_data' AND is_locked=1").fetchall()}
    cfs2025 = {r[0] for r in conn.execute("SELECT stock_code FROM financial_data WHERE year=2025 AND is_annual=1 AND report_type='CFS'").fetchall()}
    res = {"stocks": 0, "kept_same": 0, "overwritten": 0, "filled_null": 0, "inserted_rows": 0, "no_fg_value": 0, "skipped_multi": 0}
    by_field = {}
    for line in open("/tmp/fdr_finstate_q.jsonl"):
        d = json.loads(line)
        r = (d.get("rows") or {}).get(PERIOD)
        if not r: continue
        code = d["code"]; res["stocks"] += 1
        fg = {}
        for f, (k, kp) in MAP.items():
            v = r.get(k); vp = r.get(kp) if kp else None
            # Naver shows 0 for an unreported item, not a real zero -> treat as missing
            fg[f] = (None if not v else v * 1e8, None if not vp else vp * 1e8)
        rows = conn.execute("SELECT id,report_type,revenue,operating_profit,net_income,total_assets,total_equity FROM financial_data WHERE stock_code=? AND year=? AND quarter=? AND is_annual=0",
                            (code, YEAR, QTR)).fetchall()
        row = next((x for x in rows if x[1] == "CFS"), None) or (rows[0] if len(rows) == 1 else None)
        if rows and row is None: res["skipped_multi"] += 1; continue
        if row is None:
            rt = "CFS" if code in cfs2025 else "OFS"
            vals = {f: (fg[f][1] if MAP[f][1] and fg[f][1] is not None else fg[f][0]) for f in MAP}
            if all(v is None for v in vals.values()): continue
            res["inserted_rows"] += 1
            if apply:
                conn.execute("""INSERT INTO financial_data (id,stock_code,year,quarter,is_annual,report_type,revenue,operating_profit,net_income,total_assets,total_equity,data_source,updated_at)
                    VALUES ((SELECT COALESCE(MAX(id),0)+1 FROM financial_data),?,?,?,false,?,?,?,?,?,?,?,?)""",
                    (code, YEAR, QTR, rt, vals["revenue"], vals["operating_profit"], vals["net_income"], vals["total_assets"], vals["total_equity"], "fnguide_naver", now))
            continue
        id_, rt = row[0], row[1]; is_locked = (code, YEAR) in locked; unlocked = False
        for i, f in enumerate(MAP):
            v, vp = fg[f]; D = row[i + 2]
            if v is None and vp is None: res["no_fg_value"] += 1; continue
            target = vp if (MAP[f][1] and vp is not None) else v   # FnGuide basis: net income / equity = parent-attributable
            if D is not None and same(D, target): res["kept_same"] += 1; continue
            if FILL_ONLY and D is not None: res["kept_same"] += 1; continue
            L = target
            res["filled_null" if D is None else "overwritten"] += 1; by_field[f] = by_field.get(f, 0) + 1
            if not apply: continue
            if is_locked and not unlocked:
                conn.execute("UPDATE data_lock SET is_locked=0, unlocked_at=?, unlock_reason=? WHERE stock_code=? AND year=? AND table_name='financial_data'",
                             (now, f"2026-09-26 user-approved: FnGuide-basis {YEAR}Q{QTR} ({run_id})", code, YEAR)); unlocked = True
            conn.execute(f"UPDATE financial_data SET {f}=?, data_source=COALESCE(data_source,'')||'+fnguide_basis', updated_at=? WHERE id=?", (L, now, id_))
            conn.execute("""INSERT INTO financial_fix_log (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                VALUES (?,?,?,?,?,0,?,?,?,?,?,?,?)""", (now, id_, code, YEAR, QTR, rt, f, D, L,
                "FnGuide-basis: differs from DART/previous value; FnGuide adopted per user decision", "Naver FINSTATE-Q (FnGuide family) 2026-06", run_id))
            ratio = (abs(D - L) / max(abs(L), 1)) * 100 if D is not None else None
            conn.execute("INSERT INTO fnguide_dart_mismatch_log (stock_code,year,field,note,found_at) VALUES (?,?,?,?,?)",
                         (code, YEAR, f, f"FnGuide기준 채택 {YEAR}Q{QTR} {rt}: DART/기존={D} FnG={L}" + (f" ({ratio:.1f}%차)" if ratio is not None else " (기존값 없음)") + f" run={run_id}", now))
        if apply and unlocked:
            conn.execute("UPDATE data_lock SET is_locked=1, locked_at=? WHERE stock_code=? AND year=? AND table_name='financial_data'", (now, code, YEAR))
        if apply and res["stocks"] % 300 == 0: conn.commit()
    if apply: conn.commit()
    conn.close(); print({**res, "by_field": by_field, "run_id": run_id, "dry_run": not apply})

if __name__ == "__main__":
    main("--apply" in sys.argv)
