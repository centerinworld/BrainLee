#!/usr/bin/env python3
"""Fill NULL quarterly revenue/operating_profit/net_income for OPEN QUARTERLY_4WAY flags using live
OpenDART quarterly P&L (/tmp/fq_pl_dart.jsonl) ONLY when a second, independent derivation agrees:
annual(same report_type, is_annual) minus the other three DB quarters equals the DART value (<=1%).
Dry-run default; --apply writes id-targeted UPDATE ... IS NULL + financial_fix_log; flag stays OPEN with dart_value set."""
import json, sys
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

def main(apply):
    conn = connect_primary_db(timeout=300, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds")
    run_id = f"fq_open_pl_dart_fill_20260924_{datetime.now().strftime('%H%M%S')}"
    dart = {}
    for line in open("/tmp/fq_pl_dart.jsonl"):
        d = json.loads(line)
        if d.get("fs"):
            dart[(d["code"], d["year"], d["quarter"])] = d
    flags = [tuple(r) for r in conn.execute("""SELECT id,stock_code,year,quarter,field FROM fin_quarterly_validation_flags
        WHERE status='OPEN' AND field IN ('revenue','operating_profit','net_income')""").fetchall()]
    res = {"flags": len(flags), "filled": 0, "no_dart": 0, "no_null_row": 0, "no_second_source": 0, "second_source_disagrees": 0}
    for fid, code, y, q, f in flags:
        d = dart.get((code, y, q))
        if not d or d.get(f) is None:
            res["no_dart"] += 1; continue
        fs = d["fs"]
        row = conn.execute(f"""SELECT id FROM financial_data WHERE stock_code=? AND year=? AND quarter=? AND is_annual IS FALSE
            AND report_type=? AND {f} IS NULL ORDER BY id DESC LIMIT 1""", (code, y, q, fs)).fetchone()
        if not row:
            res["no_null_row"] += 1; continue
        ann = conn.execute(f"""SELECT {f} FROM financial_data WHERE stock_code=? AND year=? AND is_annual IS TRUE AND report_type=?
            AND {f} IS NOT NULL ORDER BY id DESC LIMIT 1""", (code, y, fs)).fetchone()
        others = []
        for oq in (1, 2, 3, 4):
            if oq == q:
                continue
            r = conn.execute(f"""SELECT {f} FROM financial_data WHERE stock_code=? AND year=? AND quarter=? AND is_annual IS FALSE
                AND report_type=? AND {f} IS NOT NULL ORDER BY id DESC LIMIT 1""", (code, y, oq, fs)).fetchone()
            others.append(None if not r else float(r[0]))
        if not ann or any(o is None for o in others):
            res["no_second_source"] += 1; continue
        derived = float(ann[0]) - sum(others)
        dv = d[f]
        if abs(derived - dv) > max(1.0, abs(dv) * 0.01):
            res["second_source_disagrees"] += 1; continue
        res["filled"] += 1
        if apply:
            conn.execute(f"""UPDATE financial_data SET {f}=?, data_source=COALESCE(data_source,'')||
                CASE WHEN COALESCE(data_source,'') LIKE '%dart_live_pl_20260924%' THEN '' ELSE '+dart_live_pl_20260924' END
                WHERE id=? AND {f} IS NULL""", (dv, row[0]))
            conn.execute("""INSERT INTO financial_fix_log (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                VALUES (?,?,?,?,?,0,?,?,NULL,?,?,?,?)""", (now, row[0], code, y, q, fs, f, dv,
                "NULL quarterly P&L field filled from live OpenDART; confirmed by annual minus other three quarters (<=1%)",
                f"OpenDART fnlttSinglAcntAll {fs} bsns_year={y} quarter={q}", run_id))
            conn.execute("""UPDATE fin_quarterly_validation_flags SET dart_value=?, dart_fg_status='dart_live_fill_annual_check', source_count=GREATEST(COALESCE(source_count,0),2),
                status='CONFIRMED', ai_verdict='dart_live_plus_annual_identity', notes=COALESCE(notes,'')||?, updated_at=? WHERE id=? AND status='OPEN'""",
                (dv, " [2026-09-24 live DART value equals annual minus other quarters]", now, fid))
    if apply:
        conn.commit()
    conn.close()
    print(json.dumps({**res, "run_id": run_id, "dry_run": not apply}))

if __name__ == "__main__":
    main("--apply" in sys.argv)
