#!/usr/bin/env python3
"""Restore the raw (unadjusted KRX print) basis in price_history for stocks flagged
externally_confirmed_internal_corruption where long stretches are vendor/back-adjusted or
mixed-vintage (price_history/marcap close ratio constant-ish and far from 1, ~58% of those rows
equal Naver's backward-adjusted series). Same precedent as hermes.md 000670/035720 splice repair:
replace mismatching rows (|close/marcap_close-1|>5%) with marcap raw OHLCV (integers, valid, positive
volume rules as invalid_ohlcv). Rows without a valid marcap counterpart are left untouched.
Dry-run default; --apply writes per stock with backup + a single data_fix_log row."""
from __future__ import annotations
import json, sys
from datetime import datetime
from pathlib import Path
import pandas as pd
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
from price_integrity import invalid_ohlcv  # noqa: E402
REASON = ("price_history not on raw basis (back-adjusted/mixed-vintage vs marcap unadjusted OHLCV, deviation >5%); "
          "replaced with marcap raw OHLCV per hermes.md splice-repair precedent")

def run(apply: bool) -> dict:
    conn = connect_primary_db(timeout=300)
    PRE = "--pre2018" in sys.argv
    run_id = f"marcap_raw_basis_restore_{'pre2018_' if PRE else ''}20260924_{datetime.now().strftime('%H%M%S')}"
    now = datetime.now().isoformat(timespec="seconds")
    if PRE:
        codes = sorted({r[0] for r in conn.execute("""SELECT DISTINCT stock_code FROM price_history_fix_backup
            WHERE run_id LIKE 'marcap_raw_basis_restore_20260924%'""").fetchall()})
    else:
        codes = sorted({r[0] for r in conn.execute("""SELECT stock_code FROM price_jump_audit
            WHERE classification='externally_confirmed_internal_corruption'""").fetchall()})
    mc = pd.concat([pd.read_parquet(ROOT / f"data_cache/marcap/marcap-{y}.parquet",
            columns=["Code", "Date", "Open", "High", "Low", "Close", "Volume"]) for y in (range(2010, 2018) if PRE else range(2018, 2027))])
    mc = mc[mc.Code.isin(codes)].copy()
    mc["Date"] = pd.to_datetime(mc.Date).dt.strftime("%Y-%m-%d")
    tot = {"stocks": 0, "rows": 0, "skip_invalid": 0}
    if apply:
        conn.execute("SELECT set_config('app.price_basis_checked','1',true)")
    for code in codes:
        m = mc[mc.Code == code].set_index("Date")
        plan = []
        for d, o, h, l, c, v in conn.execute("""SELECT date::text,open,high,low,close,volume FROM price_history
                WHERE stock_code=? AND close>0 AND date::text{'<' if PRE else '>='}'2018-01-01' ORDER BY date""".replace("{'<' if PRE else '>='}", '<' if PRE else '>='), (code,)).fetchall():
            d = d[:10]
            if d not in m.index:
                continue
            r = m.loc[d]
            if r.Close <= 0 or abs(c / r.Close - 1) <= 0.05:
                continue
            new = tuple(float(x) for x in (r.Open, r.High, r.Low, r.Close, r.Volume))
            if invalid_ohlcv(*new) or any(x != int(x) for x in new[:4]):
                tot["skip_invalid"] += 1; continue
            plan.append((d, tuple(float(x) if x is not None else None for x in (o, h, l, c, v)), new))
        if not plan:
            continue
        tot["stocks"] += 1; tot["rows"] += len(plan)
        if apply:
            conn.executemany("""INSERT INTO price_history_fix_backup
                (run_id,stock_code,date,old_open,old_high,old_low,old_close,old_volume,
                 new_open,new_high,new_low,new_close,new_volume,reason,fixed_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
                [(run_id, code, d, *old, *new, REASON, now) for d, old, new in plan])
            conn.executemany("""UPDATE price_history SET open=?,high=?,low=?,close=?,volume=?
                WHERE stock_code=? AND date::text=? AND close=?""", [(*new, code, d, old[3]) for d, old, new in plan])
            conn.commit()
            conn.execute("SELECT set_config('app.price_basis_checked','1',true)")
    if apply and tot["rows"]:
        conn.execute("""INSERT INTO data_fix_log
            (fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (now, "price_history", f"raw-basis restore for {tot['stocks']} stocks flagged externally_confirmed_internal_corruption",
             tot["rows"], "UPDATE price_history SET OHLCV = marcap unadjusted OHLCV WHERE |close/marcap_close-1|>5%",
             "back-adjusted / mixed-vintage prices vs raw KRX prints", "marcap raw OHLCV for same (stock_code,date)",
             "data_cache/marcap parquet (FinanceData/marcap)", run_id))
        conn.commit()
    conn.close()
    tot.update(run_id=run_id, dry_run=not apply)
    return tot

if __name__ == "__main__":
    print(json.dumps(run("--apply" in sys.argv), ensure_ascii=False, indent=2))
