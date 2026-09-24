#!/usr/bin/env python3
"""Promote the review_required corporate_action_events rows created by
record_raw_confirmed_jumps_20260924.py (source marcap_shares_daily) to factor_confirmed when a nearby
DART disclosure (-75..+10 days) of a matching type exists and the share ratio is consistent with that
type - the same rule as build_corporate_action_adjustment_engine.py (split ratio>=1.5,
merge/reduction ratio<=0.67, bonus>=1.05; backward_price_factor = old_shares/new_shares).
Dry-run default; --apply writes."""
import json, sys
from datetime import datetime, timedelta
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
from scripts.build_corporate_action_adjustment_engine import _classify_by_report  # noqa: E402

def main(apply):
    conn = connect_primary_db(timeout=120)
    now = datetime.now().isoformat(timespec="seconds")
    rows = [tuple(r) for r in conn.execute("""SELECT id,stock_code,event_date,event_type,old_shares,new_shares,share_ratio
        FROM corporate_action_events WHERE source='marcap_shares_daily' AND adjustment_status='review_required'
        AND note LIKE '%raw_confirmed_jumps_20260924%'""").fetchall()]
    res = {"candidates": len(rows), "confirmed": 0, "no_disclosure": 0, "type_inconsistent": 0, "type_collision": 0}
    detail = []
    for id_, code, ed, et, old, new, ratio in rows:
        d = datetime.strptime(str(ed)[:10], "%Y-%m-%d")
        lo, hi = (d - timedelta(days=75)).strftime("%Y%m%d"), (d + timedelta(days=10)).strftime("%Y%m%d")
        cands = conn.execute("""SELECT rcept_dt,report_nm,rcept_no,dart_url FROM dart_disclosures
            WHERE stock_code=? AND rcept_dt BETWEEN ? AND ? AND (report_nm LIKE '%분할%' OR report_nm LIKE '%병합%'
              OR report_nm LIKE '%무상증자%' OR report_nm LIKE '%감자%' OR report_nm LIKE '%자본감소%')
              AND report_nm NOT LIKE '%종속회사%' AND report_nm NOT LIKE '%결과%' ORDER BY rcept_dt DESC""", (code, lo, hi)).fetchall()
        pick = None
        for rc in cands:
            t = _classify_by_report(rc["report_nm"])
            ok = (t == "stock_split" and ratio >= 1.5) or (t == "stock_merge_or_reduction" and ratio <= 0.67) or (t == "bonus_issue" and ratio >= 1.05)
            if ok:
                pick = (t, rc); break
        if not cands:
            res["no_disclosure"] += 1; continue
        if not pick:
            res["type_inconsistent"] += 1; continue
        t, rc = pick
        if conn.execute("SELECT 1 FROM corporate_action_events WHERE stock_code=? AND event_date=? AND event_type=? AND id<>?", (code, ed, t, id_)).fetchone():
            res["type_collision"] += 1; continue
        res["confirmed"] += 1
        detail.append((code, str(ed)[:10], t, round(ratio, 4), rc["rcept_dt"], rc["report_nm"][:30]))
        if apply:
            conn.execute("""UPDATE corporate_action_events SET event_type=?, backward_price_factor=?, evidence_report_name=?,
                evidence_rcept_no=?, evidence_url=?, source='marcap_shares_daily+DART', confidence=?, adjustment_status='factor_confirmed',
                note='Confirmed event; backward raw-price factor is old_shares/new_shares (marcap shares + DART disclosure, 2026-09-24).', updated_at=?
                WHERE id=?""", (t, old / new, rc["report_nm"], rc["rcept_no"], rc["dart_url"],
                                0.9 if not rc["report_nm"].startswith("[") else 0.8, now, id_))
    if apply and res["confirmed"]:
        conn.execute("""INSERT INTO data_fix_log (fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)
            VALUES (?,?,?,?,?,?,?,?,?)""", (now, "corporate_action_events", "promote marcap-detected events with DART disclosure evidence",
            res["confirmed"], "UPDATE ... adjustment_status='factor_confirmed', backward_price_factor=old/new",
            "review_required (marcap shares only)", "factor_confirmed (marcap shares + DART disclosure type match)",
            "dart_disclosures nearby report_nm + marcap shares", f"confirm_marcap_events_dart_20260924_{datetime.now().strftime('%H%M%S')}"))
        conn.commit()
    conn.close()
    print(json.dumps(res, ensure_ascii=False)); print(detail[:40])

if __name__ == "__main__":
    main("--apply" in sys.argv)
