#!/usr/bin/env python3
"""Correct wrong values on annual financial_data rows with data_source 'dart'/'dart_live_recheck_*' for the
FIN_CROSS AMBIGUOUS flags. Live OpenDART annual statement (account_id based extract_pl + BS) is compared with the
DB dart row and the FnGuide CFS row of the same year: a field is updated to the live value only when the live value
and FnGuide agree (FnGuide is rounded to 100M won: tolerance max(0.6e8, 0.6%)) and the dart row disagrees.
Everything else is only reported. Dry-run default; --apply writes id-targeted UPDATE + financial_fix_log, and marks
the flag CONFIRMED (dart_value=live)."""
import json, sys
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scratch")); sys.path.insert(0, "/private/tmp/claude-501/-Volumes-Realtek-NVME-stock-dashboard/96abad41-5df8-45c7-8548-243cdd2d4980/scratchpad")
import legacy_dart_recollect as ldr  # noqa: E402
from fq_open_dart_fetch import bs  # noqa: E402
from db_compat import connect_primary_db  # noqa: E402

FIELDS = ("revenue", "operating_profit", "net_income", "total_assets", "total_equity")

def close(a, b, tol_abs=0.6e8, tol_rel=0.006):
    return abs(a - b) <= max(tol_abs, abs(b) * tol_rel)

def main(apply):
    conn = connect_primary_db(timeout=300, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds"); run_id = f"dart_row_live_fix_20260924_{datetime.now().strftime('%H%M%S')}"
    ldr.build_corp_map()
    flags = [tuple(r) for r in conn.execute("SELECT id,stock_code,year,field FROM cf_validation_flags WHERE status='AMBIGUOUS' AND flag_type='FIN_CROSS'").fetchall()]
    pairs = sorted({(c, y) for _, c, y, _ in flags})
    res = {"fields_fixed": 0, "flags_confirmed": 0, "reported": []}
    fixed_fields = set()
    for code, y in pairs:
        cc = ldr._CORP_MAP.get(code)
        live = None
        for fs in ("CFS", "OFS"):
            rows = ldr.fetch_dart(cc, y, "11011", fs)
            if rows:
                pl = ldr.extract_pl(rows); a, e = bs(rows)
                live = dict(fs=fs, revenue=pl["revenue"], operating_profit=pl["operating_profit"], net_income=pl["net_income"], total_assets=a, total_equity=e); break
        if not live or live["fs"] != "CFS":
            res["reported"].append((code, y, "no live CFS")); continue
        drow = conn.execute("""SELECT id,revenue,operating_profit,net_income,total_assets,total_equity FROM financial_data WHERE stock_code=? AND year=? AND is_annual IS TRUE
            AND report_type='CFS' AND (data_source='dart' OR data_source LIKE 'dart_live_recheck%') ORDER BY id DESC LIMIT 1""", (code, y)).fetchone()
        frow = conn.execute("""SELECT revenue,operating_profit,net_income,total_assets,total_equity FROM financial_data WHERE stock_code=? AND year=? AND is_annual IS TRUE
            AND report_type='CFS' AND data_source LIKE 'fnguide%' ORDER BY id DESC LIMIT 1""", (code, y)).fetchone()
        if not drow:
            res["reported"].append((code, y, "no dart row")); continue
        for i, f in enumerate(FIELDS):
            L = live[f]; D = drow[i + 1]
            if L is None or (D is not None and close(float(D), L, 1.0, 0.001)):
                continue
            F = frow[i] if frow else None
            if F is not None and close(L, float(F)):
                res["fields_fixed"] += 1; fixed_fields.add((code, y, f))
                print("FIX", code, y, f, "db", D, "->", L, "fg", F)
                if apply:
                    conn.execute(f"UPDATE financial_data SET {f}=? WHERE id=?", (L, drow[0]))
                    conn.execute("""INSERT INTO financial_fix_log (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                        VALUES (?,?,?,?,0,1,'CFS',?,?,?,?,?,?)""", (now, drow[0], code, y, f, D, L,
                        "dart-source row disagreed with live OpenDART annual statement (account_id based) which also matches FnGuide (100M-won rounding)",
                        f"OpenDART fnlttSinglAcntAll CFS bsns_year={y}", run_id))
            else:
                res["reported"].append((code, y, f, "db", D, "live", L, "fg", F))
    if apply:
        for fid, code, y, f in flags:
            if (code, y, f) in fixed_fields:
                conn.execute("UPDATE cf_validation_flags SET status='CONFIRMED',ai_verdict='dart_live_and_fnguide_agree',ai_reasoning=?,resolved_at=? WHERE id=? AND status='AMBIGUOUS'",
                             (f"2026-09-24 live DART and FnGuide agree; DB dart row corrected ({run_id})", now[:19].replace('T', ' '), fid)); res["flags_confirmed"] += 1
        conn.commit()
    conn.close()
    print(json.dumps({k: v for k, v in res.items() if k != "reported"}), "reported:", res["reported"])

if __name__ == "__main__":
    main("--apply" in sys.argv)
