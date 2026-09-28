#!/usr/bin/env python3
"""Read-only integrity audit: financial_data core fields vs OpenDART fnlttMultiAcnt (/tmp/dart_multi.jsonl). No writes.
Truth candidates per (stock, year, period, statement CFS/OFS, field) come from thstrm (that filing's own period), frmtrm (prior-year comparative,
possibly restated) and bfefrmtrm (annual only). A DB value counts as OK if it is within max(1e6 won, 0.5%) of ANY candidate.
Fields: revenue, operating_profit, net_income (TOTAL, DART '당기순이익'), total_assets, total_equity (TOTAL). Quarterly P&L = 3-month (thstrm_amount);
periods Q4 are not audited here (derived). Output: summary table + /tmp/audit_multi_mismatch.json (mismatch and DB-NULL-with-DART-value lists)."""
import json, sys, collections
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
NAMES = {"revenue": {"매출액", "수익(매출액)", "영업수익"}, "operating_profit": {"영업이익", "영업이익(손실)", "영업손실"},
         "net_income": {"당기순이익(손실)", "당기순이익", "당기순손실", "분기순이익", "반기순이익", "분기순이익(손실)", "반기순이익(손실)"},
         "total_assets": {"자산총계"}, "total_equity": {"자본총계"}}
REV = {n: f for f, ns in NAMES.items() for n in ns}
def num(s):
    s = (s or "").replace(",", "").strip()
    try: return float(s) if s and s != "-" else None
    except ValueError: return None
def main():
    truth = collections.defaultdict(list)   # (code, year, q, fs, field) -> [values]
    for line in open("/tmp/dart_multi.jsonl"):
        d = json.loads(line); rp, y = d["reprt"], d["year"]; q = 0 if rp == "11011" else {"11013": 1, "11012": 2, "11014": 3}[rp]
        for r in d["rows"]:
            f = REV.get(r["account_nm"])
            if not f: continue
            if (f in ("revenue", "operating_profit", "net_income")) != (r["sj_div"] in ("IS", "CIS")): continue
            c, fs = r["stock_code"], r["fs_div"]
            for off, key in ((0, "thstrm_amount"), (1, "frmtrm_amount"), (2, "bfefrmtrm_amount")):
                if off == 2 and q != 0: continue
                if off == 1 and q != 0 and f in ("total_assets", "total_equity"): continue  # quarterly report: BS frmtrm is the prior FISCAL YEAR-END, not the prior-year quarter
                v = num(r.get(key))
                if v is not None: truth[(c, y - off, q, fs, f)].append(v)
    conn = connect_primary_db(timeout=300, readonly=True)
    fin = {r[0] for r in conn.execute("SELECT stock_code FROM stock_universe WHERE sector_large LIKE '%금융%' OR sector_large LIKE '%보험%' OR sector_large LIKE '%은행%' OR sector_large LIKE '%증권%'").fetchall()}
    rows = conn.execute("SELECT stock_code,year,quarter,is_annual,report_type,revenue,operating_profit,net_income,total_assets,total_equity FROM financial_data WHERE year BETWEEN 2016 AND 2025").fetchall()
    conn.close()
    F = ("revenue", "operating_profit", "net_income", "total_assets", "total_equity")
    st = collections.defaultdict(collections.Counter); lists = {"mismatch": [], "db_null_dart_has": []}
    for r in rows:
        q = 0 if r[3] else r[2]
        if q == 4: continue
        grp = "금융" if r[0] in fin else "비금융"
        for i, f in enumerate(F):
            cand = truth.get((r[0], r[1], q, r[4], f))
            if not cand: st[(grp, "no DART value")]["n"] += 1; continue
            D = r[5 + i]
            if D is None:
                st[(grp, "DB NULL / DART has")]["n"] += 1
                if len(lists["db_null_dart_has"]) < 200000: lists["db_null_dart_has"].append([r[0], r[1], q, r[4], f, cand[0]])
            elif any(abs(D - v) <= max(1e6, abs(v) * 0.005) for v in cand): st[(grp, "OK (matches DART)")]["n"] += 1
            else:
                st[(grp, "MISMATCH")]["n"] += 1
                lists["mismatch"].append([r[0], r[1], q, r[4], f, D, cand[0]])
    for k in sorted(st): print(k, st[k]["n"])
    json.dump(lists, open("/tmp/audit_multi_mismatch.json", "w"))
    mm = collections.Counter((x[4]) for x in lists["mismatch"]); print("mismatch by field", dict(mm))
if __name__ == "__main__":
    main()
