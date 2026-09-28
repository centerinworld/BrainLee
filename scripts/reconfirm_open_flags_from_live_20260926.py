#!/usr/bin/env python3
"""Promote OPEN QUARTERLY_4WAY flags to CONFIRMED where the current financial_data value (row of the fetched basis CFS/OFS) equals the live OpenDART
value we fetched (assets/equity: /tmp/fq_open_dart.jsonl, /tmp/cfs_q23_live.jsonl; P&L: /tmp/fq_pl_dart.jsonl). Tolerance 0.3%. Sets dart_value, status,
notes suffix; nothing else. The original validator is not in the repo, so this only closes flags that live evidence positively confirms. Dry-run default."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
def jl(p):
    try: return [json.loads(l) for l in open(p)]
    except FileNotFoundError: return []
def main(apply):
    conn = connect_primary_db(timeout=600, readonly=not apply)
    ev = {}
    for d in jl("/tmp/fq_open_dart.jsonl"):
        if not d.get("err"):
            for f, k in (("total_assets", "assets"), ("total_equity", "equity")):
                if d.get(k) is not None: ev[(d["code"], d["year"], d["quarter"], f)] = (d["fs"], d[k])
    for d in jl("/tmp/cfs_q23_live.jsonl"):
        if not d.get("err") and d.get("total_assets") is not None and d["quarter"] in (1, 2, 3): ev.setdefault((d["code"], d["year"], d["quarter"], "total_assets"), ("CFS", d["total_assets"]))
    for d in jl("/tmp/fq_pl_dart.jsonl"):
        if not d.get("err"):
            for f, k in (("revenue", "revenue"), ("operating_profit", "operating_profit"), ("net_income", "net_income")):
                if d.get(k) is not None: ev[(d["code"], d["year"], d["quarter"], f)] = (d["fs"], d[k])
    flags = conn.execute("SELECT id,stock_code,year,quarter,field FROM fin_quarterly_validation_flags WHERE check_type='QUARTERLY_4WAY' AND status IN ('OPEN','AMBIGUOUS')").fetchall()
    n = 0; tot = 0; mism = []
    for fid, code, y, q, f in flags:
        e = ev.get((code, y, q, f))
        if not e: continue
        fs, L = e
        row = conn.execute(f"SELECT {f} FROM financial_data WHERE stock_code=? AND year=? AND quarter=? AND is_annual=0 AND report_type=?", (code, y, q, fs)).fetchall()
        if len(row) != 1 or row[0][0] is None: continue
        D = float(row[0][0]); tot += 1
        if abs(D - L) > max(abs(L) * 0.003, 1e6): mism.append((code, y, q, f, fs, D, L))
        if abs(D - L) <= max(abs(L) * 0.003, 1e6):
            n += 1
            if apply: conn.execute("UPDATE fin_quarterly_validation_flags SET status='CONFIRMED', dart_value=?, notes=COALESCE(notes,'')||' | reconfirmed vs live OpenDART fnlttSinglAcntAll ('||?||') 2026-09-26' WHERE id=?", (L, fs, fid))
    if apply: conn.commit()
    conn.close(); json.dump(mism, open("/tmp/reconfirm_mismatch.json", "w")); print({"mismatch": len(mism), "open_flags": len(flags), "comparable": tot, "confirmed": n, "dry_run": not apply})
if __name__ == "__main__":
    main("--apply" in sys.argv)
