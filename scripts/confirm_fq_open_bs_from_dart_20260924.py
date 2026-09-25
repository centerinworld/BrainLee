#!/usr/bin/env python3
"""Compare OPEN QUARTERLY_4WAY total_assets/total_equity flags with live DART balance-sheet values
(/tmp/fq_open_dart.jsonl from a read-only fnlttSinglAcntAll fetch). No financial_data changes.
DB value == DART (<=0.1%) -> flag CONFIRMED (dart_value set, source_count>=1). Different -> stays OPEN with a
note (no auto-correction). Dry-run default; --apply writes flag updates only."""
import json, sys
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

def main(apply):
    conn = connect_primary_db(timeout=300, readonly=not apply)
    now = datetime.now().isoformat(timespec="seconds")
    dart = {}
    for line in open("/tmp/fq_open_dart.jsonl"):
        d = json.loads(line)
        if d.get("fs"):
            dart[(d["code"], d["year"], d["quarter"])] = d
    flags = [tuple(r) for r in conn.execute("""SELECT id,stock_code,year,quarter,field FROM fin_quarterly_validation_flags
        WHERE status='OPEN' AND field IN ('total_assets','total_equity')""").fetchall()]
    res = {"flags": len(flags), "confirmed": 0, "differs": 0, "no_dart": 0, "no_db": 0}
    diffs = []
    for id_, code, y, q, field in flags:
        d = dart.get((code, y, q))
        if not d:
            res["no_dart"] += 1; continue
        dv = d["assets"] if field == "total_assets" else d["equity"]
        if dv is None:
            res["no_dart"] += 1; continue
        row = conn.execute(f"""SELECT {field} FROM financial_data WHERE stock_code=? AND year=? AND quarter=? AND is_annual IS FALSE
            AND report_type=? AND {field} IS NOT NULL ORDER BY id DESC LIMIT 1""", (code, y, q, d["fs"])).fetchone()
        if not row:
            res["no_db"] += 1; continue
        dbv = float(row[0])
        if abs(dbv - dv) <= max(1.0, abs(dv) * 0.001):
            res["confirmed"] += 1
            if apply:
                conn.execute("""UPDATE fin_quarterly_validation_flags SET status='CONFIRMED', dart_value=?, dart_fg_status='dart_live_match',
                    source_count=GREATEST(COALESCE(source_count,0),1), ai_verdict='dart_live_bs_match',
                    notes=?, updated_at=? WHERE id=? AND status='OPEN'""",
                    (dv, f"2026-09-24 live OpenDART fnlttSinglAcntAll ({d['fs']}) balance sheet equals DB value", now, id_))
        else:
            res["differs"] += 1; diffs.append((code, y, q, field, dbv, dv, d["fs"]))
            if apply:
                conn.execute("""UPDATE fin_quarterly_validation_flags SET dart_value=?, notes=COALESCE(notes,'')||?, updated_at=? WHERE id=? AND status='OPEN'""",
                    (dv, f" [2026-09-24 live DART {d['fs']} value {dv:.0f} differs from DB {dbv:.0f} - review]", now, id_))
    if apply:
        conn.commit()
    conn.close()
    print(json.dumps(res)); print(diffs[:12])

if __name__ == "__main__":
    main("--apply" in sys.argv)
