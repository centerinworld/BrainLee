#!/usr/bin/env python3
"""Quarterly cash flow (cash_flow_data, Q1-Q4, 2016-2025Q1) unified from OpenDART year-to-date totals (same totals FnGuide publishes; capex/depreciation are not touched).
Convention in the table: operating_cf/investing_cf/financing_cf = YEAR-TO-DATE (cumulative), *_q = single-quarter value (YTD - previous YTD; Q1_q = Q1 YTD).
Input /tmp/dart_q_full*.jsonl (ocf/icf/fcf = thstrm_amount of the cash-flow statement = YTD) and the annual cash_flow_data row (Q4 YTD = annual; Q4_q = annual - Q3 YTD).
Rows of report_type = fetched basis (CFS else OFS), every data_source variant of the stock-quarter. Overwrite when off by more than max(1e6 won, 0.3%); NULL is filled.
Logs to financial_fix_log ('cash_flow_data.<field>'); no other table is touched. Dry-run default; --apply writes."""
import glob, json, sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
KEYS = (("operating_cf", "ocf"), ("investing_cf", "icf"), ("financing_cf", "fcf"))

def close(a, b): return abs(a - b) <= max(1e6, abs(b) * 0.003)

def main(apply):
    conn = connect_primary_db(timeout=900, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds"); run_id = f"cf_quarterly_dart_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    seen = {}
    for fp in sorted(glob.glob("/tmp/dart_q_full*.jsonl")):
        for line in open(fp):
            d = json.loads(line)
            if not d.get("err") and d.get("fs"): seen[(d["code"], d["year"], d["quarter"])] = d
    years = defaultdict(dict)
    for (code, y, q), d in seen.items(): years[(code, y)][q] = d
    res = {"stock_years": 0, "changed": 0, "unchanged": 0, "filled_null": 0}
    for n, ((code, y), qs) in enumerate(years.items()):
        fs = next(iter(qs.values()))["fs"]
        if any(d["fs"] != fs for d in qs.values()): continue
        ytd = {q: {f: d.get(k) for f, k in KEYS} for q, d in qs.items()}
        ann = conn.execute("SELECT operating_cf,investing_cf,financing_cf FROM cash_flow_data WHERE stock_code=? AND year=? AND is_annual=true AND report_type=? ORDER BY id DESC LIMIT 1", (code, y, fs)).fetchone()
        if ann: ytd[4] = {f: ann[i] for i, (f, _) in enumerate(KEYS)}
        res["stock_years"] += 1
        for q in (1, 2, 3, 4):
            if q not in ytd: continue
            rows = conn.execute("SELECT id,operating_cf,investing_cf,financing_cf,operating_cf_q,investing_cf_q,financing_cf_q,value_type FROM cash_flow_data WHERE stock_code=? AND year=? AND quarter=? AND is_annual=false AND report_type=?", (code, y, q, fs)).fetchall()
            for row in rows:
                for i, (f, _) in enumerate(KEYS):
                    targets = []
                    cur = ytd[q][f]
                    if cur is not None and q != 4: targets.append((f, cur, row[i + 1]))
                    prev = ytd.get(q - 1, {}).get(f) if q > 1 else 0
                    if cur is not None and prev is not None: targets.append((f + "_q" if not f.endswith("_cf") else f + "_q", cur - prev, row[i + 4]))
                    for col, L, D in targets:
                        if D is not None and close(D, L): res["unchanged"] += 1; continue
                        res["filled_null" if D is None else "changed"] += 1
                        if not apply: continue
                        conn.execute(f"UPDATE cash_flow_data SET {col}=?, value_type=COALESCE(value_type,'cumulative→derived'), updated_at=? WHERE id=?", (L, now, row[0]))
                        conn.execute("""INSERT INTO financial_fix_log (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                            VALUES (?,?,?,?,?,0,?,?,?,?,?,?,?)""", (now, row[0], code, y, q, fs, "cash_flow_data." + col, D, L,
                            "quarterly cash flow unified: YTD from OpenDART, single-quarter = YTD - previous YTD (Q4: annual)", f"OpenDART fnlttSinglAcntAll {y} q{q} {fs}", run_id))
        if apply and n % 500 == 499: conn.commit()
    if apply: conn.commit()
    conn.close(); print({**res, "run_id": run_id, "dry_run": not apply, "keys_used": len(seen)})

if __name__ == "__main__":
    main("--apply" in sys.argv)
