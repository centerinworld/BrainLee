#!/usr/bin/env python3
"""Correct NON-financial financial_data core fields to OpenDART fnlttMultiAcnt values (/tmp/dart_multi.jsonl), the DART-basis truth (totals, incl. non-controlling).
Fields: revenue, operating_profit, net_income, total_assets, total_equity; annual and Q1-Q3 (Q4 is derived afterwards), 2016-2025. Financial-sector stocks are skipped
(FnGuide basis by user decision). A row of (stock, period, report_type CFS/OFS) is compared with the DART statement of the SAME type (fs_div).
Truth selection per (stock, year, period, fs_div, field): 'own' = thstrm of that period's own filing, else the comparative (frmtrm/bfefrmtrm) of a later filing.
Guards (skip, counted): several distinct own values (>0.5% apart) = ambiguous; quarterly P&L where thstrm_amount == thstrm_add_amount in Q2/Q3 (cumulative-only filer) = ambiguous.
A cell is changed when |DB - truth| > max(1e6 won, 0.5%) or when it is NULL (fill). Logs: financial_fix_log (run_id printed). data_lock stock-years: approved unlock -> update ->
relock (annual rows: lock_hash recomputed). Dry-run default; --apply writes."""
import hashlib, json, sys, collections
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
NAMES = {"revenue": {"매출액", "수익(매출액)", "영업수익"}, "operating_profit": {"영업이익", "영업이익(손실)", "영업손실"},
         "net_income": {"당기순이익(손실)", "당기순이익", "당기순손실", "분기순이익", "반기순이익", "분기순이익(손실)", "반기순이익(손실)"},
         "total_assets": {"자산총계"}, "total_equity": {"자본총계"}}
REV = {n: f for f, ns in NAMES.items() for n in ns}
F = ("revenue", "operating_profit", "net_income", "total_assets", "total_equity")
def num(s):
    s = (s or "").replace(",", "").strip()
    try: return float(s) if s and s != "-" else None
    except ValueError: return None
def tol(a, b): return abs(a - b) <= max(1e6, abs(b) * 0.005)

def build_truth():
    own = collections.defaultdict(list); cmp_ = collections.defaultdict(list); amb = set()
    for line in open("/tmp/dart_multi.jsonl"):
        d = json.loads(line); rp, y = d["reprt"], d["year"]; q = 0 if rp == "11011" else {"11013": 1, "11012": 2, "11014": 3}[rp]
        for r in d["rows"]:
            f = REV.get(r["account_nm"])
            if not f: continue
            is_pl = f in ("revenue", "operating_profit", "net_income")
            if is_pl != (r["sj_div"] in ("IS", "CIS")): continue
            c, fs = r["stock_code"], r["fs_div"]
            for off, key, addkey in ((0, "thstrm_amount", "thstrm_add_amount"), (1, "frmtrm_amount", "frmtrm_add_amount"), (2, "bfefrmtrm_amount", None)):
                if off == 2 and q != 0: continue
                if off == 1 and q != 0 and not is_pl: continue          # quarterly BS comparative = prior fiscal year-end, not the prior-year quarter
                v = num(r.get(key))
                if v is None: continue
                k = (c, y - off, q, fs, f)
                if is_pl and q in (2, 3) and addkey and num(r.get(addkey)) is not None and abs(num(r.get(addkey)) - v) < 1: amb.add(k); continue
                (own if off == 0 else cmp_)[k].append(v)
    truth = {}
    for k in set(own) | set(cmp_):
        if k in amb: continue
        vals = own.get(k) or cmp_.get(k)
        if any(not tol(a, vals[0]) for a in vals): continue     # ambiguous: distinct values
        truth[k] = vals[0]
    return truth, len(amb)

def main(apply):
    truth, n_amb = build_truth()
    conn = connect_primary_db(timeout=1800, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds"); run_id = f"dart_multi_truth_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    fin = {r[0] for r in conn.execute("SELECT stock_code FROM stock_universe WHERE sector_large LIKE '%금융%' OR sector_large LIKE '%보험%' OR sector_large LIKE '%은행%' OR sector_large LIKE '%증권%'").fetchall()}
    locked = {(r[0], r[1]) for r in conn.execute("SELECT stock_code,year FROM data_lock WHERE table_name='financial_data' AND is_locked=1").fetchall()}
    rows = conn.execute("SELECT id,stock_code,year,quarter,is_annual,report_type,revenue,operating_profit,net_income,total_assets,total_equity FROM financial_data WHERE year BETWEEN 2016 AND 2025").fetchall()
    res = collections.Counter(); by = collections.Counter(); unlocked = set(); annual = set()
    for row in rows:
        code, y, q, ann, rt = row[1], row[2], 0 if row[4] else row[3], row[4], row[5]
        if code in fin or q == 4: continue
        for i, f in enumerate(F):
            T = truth.get((code, y, q, rt, f))
            if T is None: res["no_truth_or_ambiguous"] += 1; continue
            D = row[6 + i]
            if D is not None and tol(D, T): res["ok"] += 1; continue
            kind = "fill" if D is None else "correct"
            res[kind] += 1; by[(f, kind)] += 1
            if not apply: continue
            if (code, y) in locked and (code, y) not in unlocked:
                conn.execute("UPDATE data_lock SET is_locked=0, unlocked_at=?, unlock_reason=? WHERE stock_code=? AND year=? AND table_name='financial_data'",
                             (now, f"2026-09-26 user-approved: correct to OpenDART multi-account values ({run_id})", code, y)); unlocked.add((code, y))
            conn.execute(f"UPDATE financial_data SET {f}=?, updated_at=? WHERE id=?", (T, now, row[0]))
            conn.execute("""INSERT INTO financial_fix_log (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (now, row[0], code, y, row[3], 1 if ann else 0, rt, f, D, T,
                "corrected to OpenDART fnlttMultiAcnt statement value (same statement type; DART-basis totals)", "OpenDART fnlttMultiAcnt", run_id))
            if ann: annual.add((code, y))
    if apply:
        for code, y in unlocked:
            h = conn.execute("SELECT revenue,operating_profit,net_income FROM financial_data WHERE stock_code=? AND year=? AND is_annual=true ORDER BY (CASE WHEN report_type='CFS' THEN 0 ELSE 1 END),(CASE WHEN data_source LIKE 'dart%' THEN 0 ELSE 1 END),id LIMIT 1", (code, y)).fetchone()
            new_hash = hashlib.md5(f"{h[0]}|{h[1]}|{h[2]}".encode()).hexdigest() if h and (code, y) in annual else None
            conn.execute("UPDATE data_lock SET is_locked=1, locked_at=?, lock_hash=COALESCE(?, lock_hash) WHERE stock_code=? AND year=? AND table_name='financial_data'", (now, new_hash, code, y))
        conn.commit()
    conn.close(); print(dict(res), {f"{k[0]}:{k[1]}": v for k, v in sorted(by.items())}, "ambiguous_cumulative_only_keys", n_amb, run_id, "DRY" if not apply else "APPLIED")

if __name__ == "__main__":
    main("--apply" in sys.argv)
