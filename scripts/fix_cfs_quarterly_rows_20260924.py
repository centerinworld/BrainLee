#!/usr/bin/env python3
"""Correct CFS quarterly financial_data rows (2023+) using two independent sources that agree with each other:
  * Naver quarterly finstate via FinanceDataReader (NAVER/FINSTATE-Q, 100M-won rounded)  -> /tmp/q_suspicious.json (pre-screen)
  * live OpenDART fnlttSinglAcntAll (unambiguous account_id; Q1-Q3 = thstrm_amount 3-month, Q4 = annual - 9M cumulative) -> /tmp/q_live.jsonl
A field is corrected to the DART value only when |dart - naver| <= max(1.2e8, 2%) (the two sources agree) and the DB value is outside
max(0.6e8, 0.6%) of the DART value. Scope: revenue, operating_profit, total_assets (net income / equity are excluded: parent-attributable
vs total definitions). DART fs_div must be CFS. id-targeted UPDATE with old-value guard + financial_fix_log. Dry-run default."""
import json, sys
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
FIELDS = ("revenue", "operating_profit", "total_assets")

def main(apply):
    conn = connect_primary_db(timeout=600, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds"); run_id = f"cfs_quarterly_fix_20260924_{datetime.now().strftime('%H%M%S')}"
    live = {}
    for l in open("/tmp/q_live.jsonl"):
        d = json.loads(l)
        if not d.get("err") and d.get("fs") == "CFS": live[(d["code"], d["year"], d["quarter"])] = d
    def lv(d, f):
        if d["quarter"] == 4:
            return {"revenue": d.get("q4_revenue"), "operating_profit": d.get("q4_op")}.get(f, d.get(f))
        return d.get(f)
    locked = {(r[0], r[1]) for r in conn.execute("SELECT stock_code,year FROM data_lock WHERE table_name='financial_data' AND is_locked=1").fetchall()}
    res = {"fixed": 0, "sources_disagree": 0, "db_within_rounding": 0, "no_live": 0, "changed_since": 0, "out_of_scope": 0, "locked": 0}; by = {}
    for id_, code, y, q, f, D, N, src in json.load(open("/tmp/q_suspicious.json")):
        if f not in FIELDS: res["out_of_scope"] += 1; continue
        d = live.get((code, y, q)); L = lv(d, f) if d else None
        if L is None: res["no_live"] += 1; continue
        if abs(L - N) > max(1.2e8, abs(N) * 0.02): res["sources_disagree"] += 1; continue
        if abs(D - L) <= max(0.6e8, abs(L) * 0.006): res["db_within_rounding"] += 1; continue
        if (code, y) in locked: res["locked"] += 1; continue
        cur = conn.execute(f"SELECT {f} FROM financial_data WHERE id=?", (id_,)).fetchone()
        if cur is None or cur[0] is None or abs(float(cur[0]) - D) > 1e-6: res["changed_since"] += 1; continue
        res["fixed"] += 1; by[f] = by.get(f, 0) + 1
        if apply:
            conn.execute(f"UPDATE financial_data SET {f}=? WHERE id=?", (L, id_))
            conn.execute("""INSERT INTO financial_fix_log (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                VALUES (?,?,?,?,?,0,'CFS',?,?,?,?,?,?)""", (now, id_, code, y, q, f, D, L,
                "CFS quarterly row disagreed with live OpenDART (account_id) and Naver quarterly finstate, which agree with each other",
                f"OpenDART fnlttSinglAcntAll CFS bsns_year={y} quarter={q}; naver={N}; prev_source={src}", run_id))
    if apply: conn.commit()
    conn.close(); print(json.dumps({**res, "by_field": by, "run_id": run_id, "dry_run": not apply}))

if __name__ == "__main__":
    main("--apply" in sys.argv)
