#!/usr/bin/env python3
"""Repair isolated price_history corruption runs flagged externally_confirmed_internal_corruption
where the independent, unadjusted marcap series is smooth (price_history jumps, marcap does not).
For each flagged event: walk forward from event_date over consecutive price_history rows whose close
deviates >5% from marcap close (max 40 days) - the run must end with a row matching marcap within 5%
(snap-back) - and replace those rows' OHLCV with marcap integer OHLCV (only rows where marcap has a
positive close and integer prices). Dry-run default; --apply writes with backup + data_fix_log."""
from __future__ import annotations
import json, sys
from datetime import datetime
from pathlib import Path
import pandas as pd
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
from price_integrity import invalid_ohlcv  # noqa: E402
REASON = ("isolated price_history corruption run (marcap unadjusted OHLCV smooth, price_history jumps >5% and snaps back); "
          "replaced with marcap OHLCV")

def run(apply: bool) -> dict:
    conn = connect_primary_db(timeout=300)
    run_id = f"marcap_snapback_repair_20260924_{datetime.now().strftime('%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    events = [tuple(r) for r in conn.execute("""SELECT stock_code,event_date FROM price_jump_audit
        WHERE classification='externally_confirmed_internal_corruption'""").fetchall()]
    dfs = {}
    tot = {"events": len(events), "runs": 0, "rows": 0, "skip_no_marcap": 0, "skip_no_snapback": 0, "skip_invalid": 0}
    plan, seen = [], set()
    for code, ed in events:
        y = int(ed[:4])
        for yy in (y - 1, y, y + 1):
            f = ROOT / f"data_cache/marcap/marcap-{yy}.parquet"
            if yy not in dfs and f.exists():
                dfs[yy] = pd.read_parquet(f, columns=["Code", "Date", "Open", "High", "Low", "Close", "Volume"])
        m = pd.concat([dfs[yy][dfs[yy].Code == code] for yy in (y - 1, y, y + 1) if yy in dfs])
        if m.empty:
            tot["skip_no_marcap"] += 1; continue
        m = m.assign(Date=pd.to_datetime(m.Date).dt.strftime("%Y-%m-%d")).set_index("Date")
        ph = [tuple(r) for r in conn.execute("""SELECT date::text,open,high,low,close,volume FROM price_history
            WHERE stock_code=? AND date::text >= (?::date - 60)::text AND date::text <= (?::date + 60)::text AND close>0
            ORDER BY date""", (code, ed, ed)).fetchall()]
        flags = []
        for d, o, h, l, c, v in ph:
            d = d[:10]
            mc = float(m.loc[d, "Close"]) if d in m.index else 0.0
            flags.append(None if mc <= 0 else abs(c / mc - 1) > 0.05)
        idx = next((i for i, r in enumerate(ph) if r[0][:10] == ed), None)
        if idx is None:
            tot["skip_no_snapback"] += 1; continue
        # anchor on a deviating row at or just before event date
        a = idx if flags[idx] else (idx - 1 if idx > 0 and flags[idx - 1] else None)
        if a is None:
            tot["skip_no_snapback"] += 1; continue
        lo = a
        while lo - 1 >= 0 and flags[lo - 1]:
            lo -= 1
        hi = a
        while hi + 1 < len(ph) and flags[hi + 1]:
            hi += 1
        ok_before = lo - 1 >= 0 and flags[lo - 1] is False
        ok_after = hi + 1 < len(ph) and flags[hi + 1] is False
        if not (ok_before and ok_after) or hi - lo + 1 > 40:
            tot["skip_no_snapback"] += 1; continue
        run_rows = [(r[0][:10], *r[1:]) for r in ph[lo:hi + 1]]
        tot["runs"] += 1
        for d, o, h, l, c, v in run_rows:
            if (code, d) in seen:
                continue
            seen.add((code, d))
            mr = m.loc[d]
            new = tuple(float(x) for x in (mr.Open, mr.High, mr.Low, mr.Close, mr.Volume))
            if invalid_ohlcv(*new) or any(x != int(x) for x in new[:4]):
                tot["skip_invalid"] += 1; continue
            plan.append((code, d, tuple(float(x) if x is not None else None for x in (o, h, l, c, v)), new))
    tot["rows"] = len(plan)
    if apply and plan:
        conn.execute("SELECT set_config('app.price_basis_checked','1',true)")
        conn.executemany("""INSERT INTO price_history_fix_backup
            (run_id,stock_code,date,old_open,old_high,old_low,old_close,old_volume,
             new_open,new_high,new_low,new_close,new_volume,reason,fixed_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
            [(run_id, c, d, *old, *new, REASON, now) for c, d, old, new in plan])
        conn.executemany("""UPDATE price_history SET open=?,high=?,low=?,close=?,volume=?
            WHERE stock_code=? AND date::text=? AND close=?""",
            [(*new, c, d, old[3]) for c, d, old, new in plan])
        conn.execute("""INSERT INTO data_fix_log
            (fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (now, "price_history", f"externally_confirmed_internal_corruption runs, {tot['runs']} runs (marcap smooth, snap-back verified)",
             len(plan), "UPDATE price_history SET OHLCV = marcap OHLCV for corrupted run rows",
             "close deviates >5% from marcap unadjusted close inside a snap-back run",
             "marcap integer OHLCV for same (stock_code,date)", "data_cache/marcap parquet (FinanceData/marcap)", run_id))
        conn.commit()
    conn.close()
    tot.update(run_id=run_id, dry_run=not apply)
    return tot

if __name__ == "__main__":
    print(json.dumps(run("--apply" in sys.argv), ensure_ascii=False, indent=2))
