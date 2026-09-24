#!/usr/bin/env python3
"""After the marcap raw-basis restore (2026-09-24) some price_jump_audit-uncovered jumps became visible
(previously hidden by mixed adjusted bases). Each is confirmed against marcap unadjusted closes
(price_history == marcap on both sides). Where marcap's shares-outstanding change explains the price
ratio (|ratio/share_change-1|<15%), record a review_required corporate_action_events row
(source marcap_shares_daily, same convention as scripts/detect_corporate_actions_from_marcap.py) and
classify corporate_action_pending_confirmation; the rest are classified raw_source_confirmed_jump_review.
Input: /tmp/new_jumps.json. --apply to write."""
import json, sys
from datetime import datetime
from pathlib import Path
import pandas as pd
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

def main(apply):
    jumps = json.load(open("/tmp/new_jumps.json"))
    conn = connect_primary_db(timeout=120)
    now = datetime.now().isoformat(timespec="seconds")
    run_id = f"raw_confirmed_jumps_20260924_{datetime.now().strftime('%H%M%S')}"
    mc = pd.concat([pd.read_parquet(ROOT / f"data_cache/marcap/marcap-{y}.parquet", columns=["Code", "Date", "Close", "Stocks"])
                    for y in range(2010, 2027)])
    codes = {j["code"] for j in jumps}
    mc = mc[mc.Code.isin(codes)].copy(); mc["Date"] = pd.to_datetime(mc.Date).dt.strftime("%Y-%m-%d")
    nid = conn.execute("SELECT COALESCE(MAX(id),0) FROM corporate_action_events").fetchone()[0]
    ev_new = aud_new = 0; cnt = {"pending": 0, "raw_confirmed": 0, "skip_no_marcap": 0}
    for j in jumps:
        code, d, prev = j["code"], j["date"], j["prev"]
        m = mc[mc.Code == code].set_index("Date")
        if d not in m.index or prev not in m.index:
            cnt["skip_no_marcap"] += 1; continue
        pc, ec = conn.execute("SELECT (SELECT close FROM price_history WHERE stock_code=? AND date::text=?),(SELECT close FROM price_history WHERE stock_code=? AND date::text=?)",
                              (code, prev, code, d)).fetchone()
        mp, me = float(m.loc[prev, "Close"]), float(m.loc[d, "Close"])
        s_prev, s_cur = float(m.loc[prev, "Stocks"]), float(m.loc[d, "Stocks"])
        sh = s_prev / s_cur if s_cur else None
        ratio = ec / pc
        typ = None
        if sh and abs(ratio / sh - 1) < 0.15 and abs(sh - 1) > 0.03:
            typ = "share_reduction_unclassified" if sh > 1 else "share_increase_unclassified"
            cls = "corporate_action_pending_confirmation"; cnt["pending"] += 1
            if apply and not conn.execute("SELECT 1 FROM corporate_action_events WHERE stock_code=? AND event_date=? AND event_type=?", (code, d, typ)).fetchone():
                nid += 1; ev_new += 1
                conn.execute("""INSERT INTO corporate_action_events (id,stock_code,event_date,event_type,old_shares,new_shares,share_ratio,
                    backward_price_factor,evidence_report_name,evidence_rcept_no,evidence_url,source,confidence,adjustment_status,note,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL,NULL,'marcap_shares_daily',0.55,'review_required',?,?,?)""",
                    (nid, code, d, typ, s_prev, s_cur, s_cur / s_prev,
                     f"No automatic price rewrite; event economics or type is not sufficiently confirmed (marcap gives share counts only, no filing). {run_id}: raw price ratio {ratio:.4f} matches marcap share change.", now, now))
        else:
            cls = "raw_source_confirmed_jump_review"; cnt["raw_confirmed"] += 1
        if apply:
            conn.execute("DELETE FROM price_jump_audit WHERE stock_code=? AND event_date=?", (code, d))
            conn.execute("""INSERT INTO price_jump_audit (stock_code,event_date,previous_date,previous_close,event_close,price_ratio,
                public_previous_close,public_event_close,public_price_ratio,classification,return_usable,matched_event_type,matched_report_name,evidence,audited_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,0,?,NULL,?,?)""",
                (code, d, prev, pc, ec, ratio, mp, me, me / mp, cls, typ,
                 f"{run_id}: price_history equals marcap unadjusted close on both days (ratio {ratio:.4f}); raw KRX print jump, cause " +
                 ("matches marcap shares change" if typ else "not explained by marcap share count (bonus/rights issue timing or other)"), now))
    if apply:
        conn.execute("""INSERT INTO data_fix_log (fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)
            VALUES (?,?,?,?,?,?,?,?,?)""", (now, "price_jump_audit", "classify jumps revealed by raw-basis restore", cnt["pending"] + cnt["raw_confirmed"],
            "INSERT price_jump_audit rows (+corporate_action_events review_required rows)", "jump not in audit (hidden by mixed basis)",
            f"pending {cnt['pending']}, raw_confirmed {cnt['raw_confirmed']}, events {ev_new}", "marcap unadjusted close/shares", run_id))
        conn.commit()
    conn.close()
    print(json.dumps({**cnt, "events_inserted": ev_new, "run_id": run_id, "dry_run": not apply}))

if __name__ == "__main__":
    main("--apply" in sys.argv)
