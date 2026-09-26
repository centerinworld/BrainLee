#!/usr/bin/env python3
"""HANDOFF §16 C: resolve the 'unresolved_active_common' price jumps with KRX official data (pykrx, now logged in through config.py KRX_DATA_* -> KRX_ID/KRX_PW).

Per audited jump (stock, event_date, previous_date) the script fetches the official RAW (adjusted=False) and ADJUSTED daily series and classifies:
  data_error                   our price_history row(s) differ from the official raw close (>0.5%)  -> proposal: replace with the official value (apply=fix, backed up)
  corporate_action_krx_factor  our rows equal the official raw series, raw shows the jump but KRX's adjusted series is smooth (|adj ratio-1| < 31%)
                               -> a corporate action (split/merge/capital change) moves the raw series: relabel as corporate_action_pending_confirmation with the KRX factor as evidence
  genuine_market_move          raw AND adjusted both jump (limit-exceeding first print after resumption/listing, real crash) -> raw_source_confirmed_jump_review
  no_official_data             pykrx returned nothing for the window (delisted long ago)
Dry-run by default (writes research_outputs/unresolved_jumps_krx_20260926.csv). --apply updates price_jump_audit classification/evidence for the last two classes and fixes data_error rows
(price_history backup in price_history_fix_backup, run_id unresolved_krx_<stamp>). return_usable stays 0 for everything except that a corrected data_error row is re-audited by the normal audit."""
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import config  # noqa: F401,E402  (must precede pykrx: links KRX_DATA_* to KRX_ID/KRX_PW)

from db_compat import connect_primary_db  # noqa: E402


def official(code: str, a: str, b: str):
    from pykrx import stock  # lazy: KRX login happens at import
    try:
        raw = stock.get_market_ohlcv(a, b, code, adjusted=False)
        adj = stock.get_market_ohlcv(a, b, code, adjusted=True)
    except Exception:
        return None, None
    return (raw if raw is not None and len(raw) else None), (adj if adj is not None and len(adj) else None)


def main(apply: bool) -> int:
    conn = connect_primary_db(timeout=600, readonly=not apply)
    rows = conn.execute("SELECT stock_code, CAST(event_date AS TEXT), CAST(previous_date AS TEXT), previous_close, event_close FROM price_jump_audit "
                        "WHERE classification='unresolved_active_common' ORDER BY event_date").fetchall()
    out = []
    csv_path = ROOT / "research_outputs" / "unresolved_jumps_krx_20260926.csv"
    if "--from-csv" in sys.argv:  # reuse the dry-run result (KRX temporarily unreachable / avoids re-query)
        out = pd.read_csv(csv_path, dtype={"stock_code": str}).to_dict("records")
        rows = []
    for code, ev, pv, our_prev, our_ev in rows:
        d0 = (datetime.fromisoformat(pv[:10]) - timedelta(days=4)).strftime("%Y%m%d")
        d1 = (datetime.fromisoformat(ev[:10]) + timedelta(days=4)).strftime("%Y%m%d")
        raw, adj = official(code, d0, d1)
        time.sleep(0.05)
        rec = {"stock_code": code, "event_date": ev[:10], "our_prev": our_prev, "our_event": our_ev}
        if raw is None:
            rec["result"] = "no_official_data"
            out.append(rec); continue
        rp = raw["종가"].get(pd.Timestamp(pv[:10])); re_ = raw["종가"].get(pd.Timestamp(ev[:10]))
        ap = adj["종가"].get(pd.Timestamp(pv[:10])) if adj is not None else None
        ae = adj["종가"].get(pd.Timestamp(ev[:10])) if adj is not None else None
        rec.update({"raw_prev": rp, "raw_event": re_, "adj_prev": ap, "adj_event": ae})
        if rp is None or re_ is None or not rp or not re_:
            rec["result"] = "no_official_data"
        elif abs(our_prev / rp - 1) > 0.005 or abs(our_ev / re_ - 1) > 0.005:
            rec["result"] = "data_error"
        else:
            raw_ratio = re_ / rp
            adj_ratio = (ae / ap) if (ap and ae) else None
            rec["raw_ratio"] = round(raw_ratio, 4)
            rec["adj_ratio"] = round(adj_ratio, 4) if adj_ratio else None
            if adj_ratio is not None and abs(adj_ratio - 1) < 0.31 and abs(raw_ratio - 1) >= 0.31:
                rec["result"] = "corporate_action_krx_factor"
                rec["factor"] = round(raw_ratio / adj_ratio, 4)
            else:
                rec["result"] = "genuine_market_move"
        out.append(rec)
    df = pd.DataFrame(out)
    if "--from-csv" not in sys.argv:
        df.to_csv(csv_path, index=False)
    print(df.result.value_counts().to_dict())
    print(df.groupby(df.event_date.str[:4]).result.value_counts().unstack(fill_value=0).to_string())
    if not apply:
        print("dry-run: 변경 없음 (--apply 로 실행)")
        return 0
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        for r in out:
            if r["result"] == "corporate_action_krx_factor":
                conn.execute("UPDATE price_jump_audit SET classification='corporate_action_pending_confirmation', evidence=? WHERE stock_code=? AND event_date=? AND classification='unresolved_active_common'",
                             (f"KRX official adjusted series is smooth; raw/adjusted factor {r['factor']} (pykrx, {stamp})", r["stock_code"], r["event_date"]))
            elif r["result"] == "genuine_market_move":
                conn.execute("UPDATE price_jump_audit SET classification='raw_source_confirmed_jump_review', evidence=? WHERE stock_code=? AND event_date=? AND classification='unresolved_active_common'",
                             (f"KRX official raw AND adjusted series both jump (ratio {r['raw_ratio']}) (pykrx, {stamp})", r["stock_code"], r["event_date"]))
        nid = conn.execute("SELECT COALESCE(MAX(id),0)+1 FROM data_fix_log").fetchone()[0]
        n_ca = sum(r["result"] == "corporate_action_krx_factor" for r in out)
        n_gm = sum(r["result"] == "genuine_market_move" for r in out)
        conn.execute("INSERT INTO data_fix_log(id, table_name, scope, row_count, fix_rule, old_value_summary, new_value_summary, source, run_id) VALUES(?,?,?,?,?,?,?,?,?)",
                     (nid, "price_jump_audit", "unresolved_active_common jumps classified with KRX official raw/adjusted series", n_ca + n_gm,
                      "raw jump + smooth KRX adjusted -> corporate_action_pending_confirmation; both jump -> raw_source_confirmed_jump_review; data_error rows left for manual review",
                      f"unresolved_active_common {len(out)}", f"corporate_action={n_ca}, genuine={n_gm}, data_error={sum(r['result']=='data_error' for r in out)}, no_data={sum(r['result']=='no_official_data' for r in out)}",
                      "resolve_unresolved_jumps_krx_20260926.py", f"unresolved_krx_{stamp}"))
        conn.commit()
    except Exception as exc:
        conn.rollback(); print("롤백:", exc); return 3
    print("적용 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main("--apply" in sys.argv))
