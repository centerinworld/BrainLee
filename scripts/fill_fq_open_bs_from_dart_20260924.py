#!/usr/bin/env python3
"""Fill NULL quarterly total_assets/total_equity in financial_data from live OpenDART balance-sheet values
(/tmp/fq_open_dart.jsonl) for OPEN QUARTERLY_4WAY flags whose CFS/OFS row already exists but the field is NULL.
Only NULL fields on the row whose report_type equals the DART fs_div are written (id-targeted UPDATE ... IS NULL),
logged per field in financial_fix_log; the flag keeps OPEN (single authoritative source) with dart_value set.
Dry-run default; --apply writes."""
import json, sys
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

def main(apply):
    conn = connect_primary_db(timeout=300, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds")
    run_id = f"fq_open_bs_dart_fill_20260924_{datetime.now().strftime('%H%M%S')}"
    dart = {}
    for line in open("/tmp/fq_open_dart.jsonl"):
        d = json.loads(line)
        if d.get("fs"):
            dart[(d["code"], d["year"], d["quarter"])] = d
    flags = [tuple(r) for r in conn.execute("""SELECT id,stock_code,year,quarter,field FROM fin_quarterly_validation_flags
        WHERE status='OPEN' AND field IN ('total_assets','total_equity')""").fetchall()]
    n = 0; skipped = 0
    for fid, code, y, q, field in flags:
        d = dart.get((code, y, q))
        if not d:
            continue
        dv = d["assets"] if field == "total_assets" else d["equity"]
        if dv is None:
            continue
        row = conn.execute(f"""SELECT id FROM financial_data WHERE stock_code=? AND year=? AND quarter=? AND is_annual IS FALSE
            AND report_type=? AND {field} IS NULL ORDER BY id DESC LIMIT 1""", (code, y, q, d["fs"])).fetchone()
        if not row:
            skipped += 1; continue
        n += 1
        if apply:
            conn.execute(f"""UPDATE financial_data SET {field}=?, data_source=COALESCE(data_source,'')||
                CASE WHEN COALESCE(data_source,'') LIKE '%dart_live_bs_20260924%' THEN '' ELSE '+dart_live_bs_20260924' END
                WHERE id=? AND {field} IS NULL""", (dv, row[0]))
            conn.execute("""INSERT INTO financial_fix_log (fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                VALUES (?,?,?,?,?,0,?,?,NULL,?,?,?,?)""", (now, row[0], code, y, q, d["fs"], field, dv,
                "NULL balance-sheet field filled from live OpenDART fnlttSinglAcntAll (OPEN flag, no other source)",
                f"OpenDART fnlttSinglAcntAll {d['fs']} bsns_year={y} quarter={q}", run_id))
            conn.execute("""UPDATE fin_quarterly_validation_flags SET dart_value=?, dart_fg_status='dart_live_fill', source_count=GREATEST(COALESCE(source_count,0),1),
                notes=COALESCE(notes,'')||?, updated_at=? WHERE id=?""", (dv, " [2026-09-24 field filled from live DART; single authoritative source]", now, fid))
    if apply:
        conn.commit()
    conn.close()
    print(json.dumps({"filled": n, "skipped_no_null_row": skipped, "run_id": run_id, "dry_run": not apply}))

if __name__ == "__main__":
    main("--apply" in sys.argv)
