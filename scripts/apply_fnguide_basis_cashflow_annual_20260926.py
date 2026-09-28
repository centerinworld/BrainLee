#!/usr/bin/env python3
"""FnGuide-basis reconciliation of ANNUAL cash_flow_data (2021-2025) from Naver's FnGuide-family annual statements (/tmp/fdr_finstate_a.jsonl;
영업/투자/재무활동현금흐름, CAPEX in 억원, same sign convention as the DB: capex positive). CFS rows (else OFS if no CFS row) of December fiscal years.
same within rounding (|diff| <= 1e8 won or 0.5%) -> keep; different -> FnGuide value + financial_fix_log ('cash_flow_data.<field>' in field_name,
row_id = cash_flow_data.id) + fnguide_dart_mismatch_log; NULL -> filled; missing CFS annual row -> inserted (data_source 'fnguide_naver').
Also with --dart: 2016-2020 (and 2021 NULL fills) operating/investing/financing_cf from /tmp/dart_a_full.jsonl (ifrs CashFlowsFromUsedIn..., OFS/CFS as fetched).
Dry-run default; --apply writes."""
import json, sys
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
FIELDS = {"operating_cf": "영업활동현금흐름", "investing_cf": "투자활동현금흐름", "financing_cf": "재무활동현금흐름", "capex": "CAPEX"}

def same(a, b):
    d = abs(a - b)
    return d <= 1e8 or d <= abs(b) * 0.005

def upd(conn, apply, res, by, now, run_id, row_id, code, y, rt, f, D, L, src, mode):
    res["filled_null" if D is None else "overwritten"] += 1; by[f] = by.get(f, 0) + 1
    if not apply: return
    conn.execute(f"UPDATE cash_flow_data SET {f}=?, updated_at=? WHERE id=?", (L, now, row_id))
    conn.execute("""INSERT INTO financial_fix_log (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
        VALUES (?,?,?,?,0,1,?,?,?,?,?,?,?)""", (now, row_id, code, y, rt, "cash_flow_data." + f, D, L, mode, src, run_id))
    if D is not None:
        conn.execute("INSERT INTO fnguide_dart_mismatch_log (stock_code,year,field,note,found_at) VALUES (?,?,?,?,?)",
                     (code, y, "cf." + f, f"FnGuide기준 채택 현금흐름 {rt}: 기존={D} 채택={L} ({abs(D-L)/max(abs(L),1)*100:.1f}%차) run={run_id}", now))

def main(apply, use_dart):
    conn = connect_primary_db(timeout=900, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds"); run_id = f"fnguide_basis_cf_annual_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    FIN = {r[0] for r in conn.execute("SELECT stock_code FROM stock_universe WHERE sector_large LIKE '%금융%' OR sector_large LIKE '%보험%' OR sector_large LIKE '%은행%' OR sector_large LIKE '%증권%'").fetchall()}
    res = {"stock_years": 0, "kept_same": 0, "overwritten": 0, "filled_null": 0, "inserted_rows": 0}; by = {}
    def cf_rows(code, y):
        rows = conn.execute("SELECT id,report_type,operating_cf,investing_cf,financing_cf,capex FROM cash_flow_data WHERE stock_code=? AND year=? AND is_annual=true", (code, y)).fetchall()
        return [x for x in rows if x[1] == "CFS"] or rows
    if not use_dart:
        for n, line in enumerate(open("/tmp/fdr_finstate_a.jsonl")):
            d = json.loads(line); code = d["code"]
            for date, r in (d.get("rows") or {}).items():
                if not date[:4].isdigit(): continue
                y, m = int(date[:4]), int(date[5:7])
                if not 2021 <= y <= 2025 or m != 12: continue
                fg = {f: (None if not r.get(k) else r[k] * 1e8) for f, k in FIELDS.items()}
                if all(v is None for v in fg.values()): continue
                use = cf_rows(code, y); res["stock_years"] += 1
                if not use:
                    res["inserted_rows"] += 1
                    if apply:
                        conn.execute("""INSERT INTO cash_flow_data (id,stock_code,year,quarter,is_annual,operating_cf,investing_cf,financing_cf,capex,report_type,data_source,updated_at)
                            VALUES ((SELECT COALESCE(MAX(id),0)+1 FROM cash_flow_data),?,?,0,true,?,?,?,?,'CFS','fnguide_naver',?)""", (code, y, fg["operating_cf"], fg["investing_cf"], fg["financing_cf"], fg["capex"], now))
                    continue
                for row in use:
                    for i, f in enumerate(FIELDS):
                        L = fg[f]; D = row[i + 2]
                        if L is None: continue
                        if D is not None and same(D, L): res["kept_same"] += 1; continue
                        upd(conn, apply, res, by, now, run_id, row[0], code, y, row[1], f, D, L, f"Naver FINSTATE (FnGuide family) {date}", "FnGuide-basis annual cash flow: differs from previous value; FnGuide adopted per user decision")
            if apply and n % 300 == 299: conn.commit()
    else:
        for line in open("/tmp/dart_a_full.jsonl"):
            d = json.loads(line)
            if d.get("err") or not d.get("fs") or not 2016 <= d["year"] <= 2021: continue
            code, y, fs = d["code"], d["year"], d["fs"]
            if code in FIN: continue   # financial sector keeps the FnGuide basis (user decision 2026-09-26)
            fg = {"operating_cf": d.get("ocf"), "investing_cf": d.get("icf"), "financing_cf": d.get("fcf")}
            rows = [x for x in conn.execute("SELECT id,report_type,operating_cf,investing_cf,financing_cf,capex FROM cash_flow_data WHERE stock_code=? AND year=? AND is_annual=true AND report_type=?", (code, y, fs)).fetchall()]
            if not rows: continue
            res["stock_years"] += 1
            for row in rows:
                for i, f in enumerate(("operating_cf", "investing_cf", "financing_cf")):
                    L = fg[f]; D = row[i + 2]
                    if L is None: continue
                    if D is not None and same(D, L): res["kept_same"] += 1; continue
                    upd(conn, apply, res, by, now, run_id, row[0], code, y, fs, f, D, L, f"OpenDART fnlttSinglAcntAll {y} 11011 {fs}", "cash flow total from OpenDART annual statement (differs from previous value)")
    if apply: conn.commit()
    conn.close(); print({**res, "by_field": by, "run_id": run_id, "dry_run": not apply, "source": "dart" if use_dart else "naver"})

if __name__ == "__main__":
    main("--apply" in sys.argv, "--dart" in sys.argv)
