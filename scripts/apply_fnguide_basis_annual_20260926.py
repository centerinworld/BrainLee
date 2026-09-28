#!/usr/bin/env python3
"""FnGuide-basis reconciliation of ANNUAL financial_data rows (2021-2025) from Naver's FnGuide-family annual statements
(FinanceDataReader 'NAVER/FINSTATE/<code>', 억원; /tmp/fdr_finstate_a.jsonl), per user decision 2026-09-26 ("FnGuide 기준 적용, 모든 데이터 같은 기준").
Basis: revenue; operating_profit = 영업이익(발표기준) else 영업이익; net_income = 당기순이익(지배); total_assets; total_equity = 자본총계(지배).
For each stock-year (December fiscal years only) the CFS rows (or, if the stock has no CFS annual row, the OFS rows) are compared field by field:
same within rounding (|diff| <= 1e8 won or 0.5%) -> DB value kept; different -> FnGuide value written, old value to financial_fix_log and
fnguide_dart_mismatch_log; NULL -> filled; a missing annual row is inserted (data_source 'fnguide_naver'). Naver 0 = unreported -> ignored.
data_lock (2019-2022, dart_verified): approved unlock -> update -> relock, lock_hash = md5('revenue|operating_profit|net_income') of the annual CFS row.
Dry-run default; --apply writes."""
import hashlib, json, sys
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
YEARS = range(2021, 2026)
FIELDS = ("revenue", "operating_profit", "net_income", "total_assets", "total_equity")

def same(a, b):
    d = abs(a - b)
    return d <= 1e8 or d <= abs(b) * 0.005

def naver_vals(r):
    def g(k):
        v = r.get(k)
        return None if not v else v * 1e8
    op = g("영업이익(발표기준)")
    if op is None: op = g("영업이익")
    ni = g("당기순이익(지배)")
    if ni is None: ni = g("당기순이익")
    eq = g("자본총계(지배)")
    if eq is None: eq = g("자본총계")
    return {"revenue": g("매출액"), "operating_profit": op, "net_income": ni, "total_assets": g("자산총계"), "total_equity": eq}

def main(apply):
    conn = connect_primary_db(timeout=900, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds"); run_id = f"fnguide_basis_annual_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    locked = {(r[0], r[1]) for r in conn.execute("SELECT stock_code,year FROM data_lock WHERE table_name='financial_data' AND is_locked=1").fetchall()}
    res = {"stock_years": 0, "kept_same": 0, "overwritten": 0, "filled_null": 0, "inserted_rows": 0, "no_fg_value": 0}
    by_field = {}
    for n, line in enumerate(open("/tmp/fdr_finstate_a.jsonl")):
        d = json.loads(line); code = d["code"]
        for date, r in (d.get("rows") or {}).items():
            if not date[:4].isdigit(): continue  # 'NaT' rows (undated estimate columns)
            y, m = int(date[:4]), int(date[5:7])
            if y not in YEARS or m != 12: continue
            fg = naver_vals(r)
            if all(v is None for v in fg.values()): continue
            rows = conn.execute("SELECT id,report_type,revenue,operating_profit,net_income,total_assets,total_equity,data_source FROM financial_data WHERE stock_code=? AND year=? AND is_annual=true",
                                (code, y)).fetchall()
            use = [x for x in rows if x[1] == "CFS"] or rows
            res["stock_years"] += 1
            if not use:
                res["inserted_rows"] += 1
                if apply:
                    conn.execute("""INSERT INTO financial_data (id,stock_code,year,quarter,is_annual,report_type,revenue,operating_profit,net_income,total_assets,total_equity,data_source,updated_at)
                        VALUES ((SELECT COALESCE(MAX(id),0)+1 FROM financial_data),?,?,0,true,'CFS',?,?,?,?,?,?,?)""",
                        (code, y, fg["revenue"], fg["operating_profit"], fg["net_income"], fg["total_assets"], fg["total_equity"], "fnguide_naver", now))
                continue
            is_locked = (code, y) in locked; unlocked = False; changed = False
            for row in use:
                for i, f in enumerate(FIELDS):
                    L = fg[f]; D = row[i + 2]
                    if L is None: res["no_fg_value"] += 1; continue
                    if D is not None and same(D, L): res["kept_same"] += 1; continue
                    res["filled_null" if D is None else "overwritten"] += 1; by_field[f] = by_field.get(f, 0) + 1
                    if not apply: continue
                    if is_locked and not unlocked:
                        conn.execute("UPDATE data_lock SET is_locked=0, unlocked_at=?, unlock_reason=? WHERE stock_code=? AND year=? AND table_name='financial_data'",
                                     (now, f"2026-09-26 user-approved: FnGuide-basis annual ({run_id})", code, y)); unlocked = True
                    conn.execute(f"UPDATE financial_data SET {f}=?, data_source=CASE WHEN COALESCE(data_source,'') LIKE '%fnguide_basis%' THEN data_source ELSE COALESCE(data_source,'')||'+fnguide_basis' END, updated_at=? WHERE id=?", (L, now, row[0]))
                    conn.execute("""INSERT INTO financial_fix_log (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                        VALUES (?,?,?,?,0,1,?,?,?,?,?,?,?)""", (now, row[0], code, y, row[1], f, D, L,
                        "FnGuide-basis annual: differs from DART/previous value; FnGuide adopted per user decision (NI/equity parent-attributable)", f"Naver FINSTATE (FnGuide family) {date}", run_id))
                    ratio = (abs(D - L) / max(abs(L), 1)) * 100 if D is not None else None
                    conn.execute("INSERT INTO fnguide_dart_mismatch_log (stock_code,year,field,note,found_at) VALUES (?,?,?,?,?)",
                                 (code, y, f, f"FnGuide기준 채택 연간 {row[1]}: DART/기존={D} FnG={L}" + (f" ({ratio:.1f}%차)" if ratio is not None else " (기존값 없음)") + f" run={run_id}", now))
                    changed = True
            if apply and unlocked:
                h = conn.execute("SELECT revenue,operating_profit,net_income FROM financial_data WHERE stock_code=? AND year=? AND is_annual=true AND report_type=? ORDER BY (CASE WHEN data_source LIKE 'dart%' THEN 0 ELSE 1 END), id LIMIT 1",
                                 (code, y, use[0][1])).fetchone()
                new_hash = hashlib.md5(f"{h[0]}|{h[1]}|{h[2]}".encode()).hexdigest() if h else None
                conn.execute("UPDATE data_lock SET is_locked=1, locked_at=?, lock_hash=COALESCE(?, lock_hash) WHERE stock_code=? AND year=? AND table_name='financial_data'", (now, new_hash, code, y))
        if apply and n % 300 == 299: conn.commit()
    if apply: conn.commit()
    conn.close(); print({**res, "by_field": by_field, "run_id": run_id, "dry_run": not apply})

if __name__ == "__main__":
    main("--apply" in sys.argv)
